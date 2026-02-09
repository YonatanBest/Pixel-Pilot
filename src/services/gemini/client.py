import logging
import random
from typing import Any, Optional

from google import genai
from google.genai import types
from google.genai.errors import ServerError
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential_jitter,
    retry_if_exception,
    before_sleep_log,
)

from services.gemini.tools import ToolRegistry, ToolExecutionError, ToolValidationError
from services.gemini.types import GenerationConfig

logger = logging.getLogger(__name__)

# Retry configuration for transient server errors
MAX_RETRIES = 5
MIN_WAIT_SECONDS = 1
MAX_WAIT_SECONDS = 32


def is_retryable_error(exception: BaseException) -> bool:
    """Check if an exception is a retryable server error (500, 502, 503, 504)."""
    if isinstance(exception, ServerError):
        # ServerError contains status_code attribute
        status_code = getattr(exception, "status_code", 0)
        return status_code in (500, 502, 503, 504)
    return False


# Retry decorator for API calls
api_retry = retry(
    retry=retry_if_exception(is_retryable_error),
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential_jitter(initial=MIN_WAIT_SECONDS, max=MAX_WAIT_SECONDS, jitter=2),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)


class GeminiError(Exception):
    """Base exception for Gemini client errors."""

    pass


class GeminiClient:
    """Wrapper around Google's Gemini API client.

    Provides:
    - Lazy client initialization
    - Support for different models per use case
    - Native function calling with automatic tool execution
    - Unified error handling and logging

    Example:
        client = GeminiClient(api_key="...", default_model="gemini-3-flash-preview")

        # Simple generation
        response = client.generate([{"role": "user", "parts": [{"text": "Hello"}]}])

        # With tools
        registry = ToolRegistry()
        registry.register(my_func, "Description")
        response = client.generate_with_tools(contents, registry)
    """

    def __init__(
        self,
        api_key: str,
        default_model: str = "gemini-3-flash-preview",
    ):
        """Initialize the Gemini client.

        Args:
            api_key: Google AI API key
            default_model: Default model to use for generation

        Raises:
            GeminiError: If API key is missing
        """
        if not api_key:
            raise GeminiError("Missing API key. Set GEMINI_API_KEY in your environment.")

        self._api_key = api_key
        self._default_model = default_model
        self._client: Optional[genai.Client] = None

    @property
    def client(self) -> genai.Client:
        """Lazy-initialized Gemini client.

        Returns:
            Initialized genai.Client instance
        """
        if self._client is None:
            self._client = genai.Client(api_key=self._api_key)
            logger.debug(f"Initialized Gemini client with model: {self._default_model}")
        return self._client

    @property
    def default_model(self) -> str:
        """Get the default model name."""
        return self._default_model

    @api_retry
    def _generate_with_retry(
        self,
        model_name: str,
        contents: list,
        config: Optional[dict] = None,
    ) -> Any:
        """Internal method that wraps generate_content with retry logic.

        This method is decorated with @api_retry to automatically handle
        transient server errors (500, 502, 503, 504) with exponential backoff.

        Args:
            model_name: The model to use for generation
            contents: Conversation contents
            config: Optional generation config

        Returns:
            Gemini API response object

        Raises:
            ServerError: If all retries are exhausted
        """
        return self.client.models.generate_content(
            model=model_name,
            contents=contents,
            config=config,
        )

    def generate(
        self,
        contents: list[dict],
        config: Optional[dict] = None,
        model: Optional[str] = None,
        tools: Optional[list] = None,
    ) -> Any:
        """Generate content using Gemini API.

        Args:
            contents: List of message dicts with 'role' and 'parts' keys
            config: Optional generation config (response_mime_type, etc.)
            model: Optional model override (uses default if not specified)
            tools: Optional list of Gemini Tool objects

        Returns:
            Gemini API response object

        Raises:
            GeminiError: If API call fails
        """
        model_name = model or self._default_model

        # Build API config
        api_config = config or {}

        try:
            response = self._generate_with_retry(
                model_name=model_name,
                contents=contents,
                config=api_config if api_config else None,
            )
            return response
        except Exception as e:
            logger.error(f"Gemini API error: {e}")
            raise GeminiError(f"Generation failed: {e}") from e

    def generate_structured(
        self,
        contents: list[dict],
        response_schema: dict,
        model: Optional[str] = None,
        thinking_level: Optional[str] = None,
        tools: Optional[list] = None,
    ) -> Any:
        """Generate structured JSON response using Gemini API.

        Args:
            contents: List of message dicts with 'role' and 'parts' keys
            response_schema: JSON Schema for the expected response
            model: Optional model override
            thinking_level: Optional thinking level ("none", "low", "medium", "high")
            tools: Optional list of Gemini Tool objects

        Returns:
            Gemini API response object

        Raises:
            GeminiError: If API call fails
        """
        model_name = model or self._default_model

        config: dict[str, Any] = {
            "response_mime_type": "application/json",
            "response_json_schema": response_schema,
        }

        if thinking_level:
            config["thinking_config"] = {"thinking_level": thinking_level}

        if tools:
            config["tools"] = tools

        try:
            response = self._generate_with_retry(
                model_name=model_name,
                contents=contents,
                config=config,
            )
            return response
        except Exception as e:
            logger.error(f"Gemini API error: {e}")
            raise GeminiError(f"Structured generation failed: {e}") from e

    def generate_with_tools(
        self,
        contents: list[dict],
        tool_registry: ToolRegistry,
        model: Optional[str] = None,
        max_tool_rounds: int = 5,
        response_schema: Optional[dict] = None,
    ) -> tuple[Any, list[dict]]:
        """Generate content with native function calling.

        Handles the tool calling loop:
        1. Send request with tool definitions
        2. If model requests tool calls, execute them
        3. Feed results back to model
        4. Repeat until model gives final response

        Args:
            contents: Initial conversation contents
            tool_registry: Registry of available tools
            model: Optional model override
            max_tool_rounds: Maximum number of tool calling rounds
            response_schema: Optional JSON schema for final response

        Returns:
            Tuple of (final response, list of tool call records)

        Raises:
            GeminiError: If API call or tool execution fails
        """
        model_name = model or self._default_model
        tool_records: list[dict] = []

        # Convert registry to Gemini Tool format
        tool_definitions = tool_registry.get_tool_definitions()
        gemini_tools = self._build_gemini_tools(tool_definitions)

        current_contents = list(contents)

        for round_num in range(max_tool_rounds):
            config: dict[str, Any] = {}

            if gemini_tools:
                config["tools"] = gemini_tools

            if response_schema:
                config["response_mime_type"] = "application/json"
                config["response_json_schema"] = response_schema

            try:
                response = self._generate_with_retry(
                    model_name=model_name,
                    contents=current_contents,
                    config=config if config else None,
                )
            except Exception as e:
                logger.error(f"Gemini API error in tool round {round_num}: {e}")
                raise GeminiError(f"Tool calling failed: {e}") from e

            # Check if model made tool calls
            if not self._has_tool_calls(response):
                # No tool calls - this is the final response
                return response, tool_records

            # Execute tool calls
            tool_results = []
            for tool_call in self._extract_tool_calls(response):
                tool_name = tool_call["name"]
                tool_args = tool_call["args"]

                record = {
                    "round": round_num,
                    "tool": tool_name,
                    "args": tool_args,
                    "result": None,
                    "error": None,
                }

                try:
                    result = tool_registry.execute(tool_name, tool_args)
                    record["result"] = result
                    tool_results.append({
                        "name": tool_name,
                        "result": str(result),
                    })
                except (ToolValidationError, ToolExecutionError) as e:
                    record["error"] = str(e)
                    tool_results.append({
                        "name": tool_name,
                        "result": f"Error: {e}",
                    })

                tool_records.append(record)

            # Add model response and tool results to conversation
            if hasattr(response, "candidates") and response.candidates:
                model_content = response.candidates[0].content
                current_contents.append({"role": "model", "parts": model_content.parts})

            # Add tool results as user message
            tool_result_parts = []
            for tr in tool_results:
                tool_result_parts.append(
                    types.Part.from_function_response(
                        name=tr["name"],
                        response={"result": tr["result"]},
                    )
                )

            if tool_result_parts:
                current_contents.append({"role": "user", "parts": tool_result_parts})

        # Exceeded max rounds
        logger.warning(f"Exceeded max tool rounds ({max_tool_rounds})")
        return response, tool_records

    def _build_gemini_tools(self, definitions: list[dict]) -> list:
        """Convert tool definitions to Gemini Tool format.

        Args:
            definitions: List of ToolDefinition dicts

        Returns:
            List of Gemini Tool objects
        """
        if not definitions:
            return []

        function_declarations = []
        for defn in definitions:
            function_declarations.append(
                types.FunctionDeclaration(
                    name=defn["name"],
                    description=defn["description"],
                    parameters=defn["parameters"],
                )
            )

        return [types.Tool(function_declarations=function_declarations)]

    def _has_tool_calls(self, response: Any) -> bool:
        """Check if response contains tool calls.

        Args:
            response: Gemini API response

        Returns:
            True if response contains function calls
        """
        if not hasattr(response, "candidates") or not response.candidates:
            return False

        content = response.candidates[0].content
        if not hasattr(content, "parts"):
            return False

        for part in content.parts:
            if hasattr(part, "function_call") and part.function_call:
                return True

        return False

    def _extract_tool_calls(self, response: Any) -> list[dict]:
        """Extract tool calls from response.

        Args:
            response: Gemini API response

        Returns:
            List of dicts with 'name' and 'args' keys
        """
        calls = []

        if not hasattr(response, "candidates") or not response.candidates:
            return calls

        content = response.candidates[0].content
        if not hasattr(content, "parts"):
            return calls

        for part in content.parts:
            if hasattr(part, "function_call") and part.function_call:
                fc = part.function_call
                calls.append({
                    "name": fc.name,
                    "args": dict(fc.args) if fc.args else {},
                })

        return calls

    def __repr__(self) -> str:
        """String representation for debugging."""
        return f"GeminiClient(model={self._default_model})"
