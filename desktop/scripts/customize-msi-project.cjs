const fs = require('node:fs');

const MARKER_START = '<!-- PixelPilot MSI customization start -->';
const MARKER_END = '<!-- PixelPilot MSI customization end -->';
const TASK_NAMES = {
  orchestrator: 'PixelPilot Orchestrator',
  agent: 'PixelPilot UAC Agent',
};

function xmlAttr(value) {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&apos;');
}

function replaceOrThrow(source, pattern, replacement, label) {
  if (!pattern.test(source)) {
    throw new Error(`Unable to find ${label} in MSI project.`);
  }
  return source.replace(pattern, replacement);
}

function stripExistingCustomization(source) {
  return source
    .replace(new RegExp(`\\s*${MARKER_START}[\\s\\S]*?${MARKER_END}\\s*`, 'g'), '\n')
    .replace(/\s*<ComponentGroup Id="DesktopShortcutComponents"[\s\S]*?<\/ComponentGroup>\s*/g, '\n');
}

function quoteForTask(targetPath) {
  return `\\"${targetPath}\\"`;
}

function taskCreateCommand(taskName, targetPath) {
  return `schtasks /Create /F /SC ONSTART /RU SYSTEM /RL HIGHEST /TN "${taskName}" /TR "${quoteForTask(targetPath)}"`;
}

function taskDeleteCommand(taskName) {
  return `schtasks /Delete /TN "${taskName}" /F >NUL 2>&1`;
}

function buildInstallTasksCommand() {
  const orchestratorPath = '[APPLICATIONFOLDER]resources\\runtime\\orchestrator.exe';
  const agentPath = '[APPLICATIONFOLDER]resources\\runtime\\agent.exe';
  return `"[%ComSpec]" /c ${taskCreateCommand(TASK_NAMES.orchestrator, orchestratorPath)} && ${taskCreateCommand(TASK_NAMES.agent, agentPath)}`;
}

function buildRemoveTasksCommand() {
  return `"[%ComSpec]" /c ${taskDeleteCommand(TASK_NAMES.orchestrator)} & ${taskDeleteCommand(TASK_NAMES.agent)} & exit /b 0`;
}

function buildFeatureBlock(productName) {
  return [
    `<Feature Id="ProductFeature" Title="${xmlAttr(productName)}" Display="expand" Absent="disallow">`,
    '  <ComponentGroupRef Id="ProductComponents"/>',
    `  <Feature Id="DesktopShortcutFeature" Title="Desktop shortcut" Description="Create a desktop shortcut for ${xmlAttr(productName)}." Level="2" AllowAdvertise="no">`,
    '    <ComponentGroupRef Id="DesktopShortcutComponents"/>',
    '  </Feature>',
    '</Feature>',
  ].join('\n    ');
}

function buildShortcutComponentGroup(productName, iconId) {
  const iconAttributes = iconId ? ` Icon="${xmlAttr(iconId)}" IconIndex="0"` : '';
  return `
    <ComponentGroup Id="DesktopShortcutComponents" Directory="DesktopFolder">
      <Component Id="DesktopShortcutComponent">
        <Shortcut Id="desktopShortcut" Directory="DesktopFolder" Name="${xmlAttr(productName)}" Description="Open ${xmlAttr(productName)}" Target="[#mainExecutable]" WorkingDirectory="APPLICATIONFOLDER"${iconAttributes} Advertise="no"/>
        <RegistryValue Root="HKCU" Key="Software\\${xmlAttr(productName)}" Name="DesktopShortcut" Type="integer" Value="1" KeyPath="yes"/>
      </Component>
    </ComponentGroup>`;
}

function buildCustomActionBlock() {
  const installCommand = xmlAttr(buildInstallTasksCommand());
  const removeCommand = xmlAttr(buildRemoveTasksCommand());
  return `
    ${MARKER_START}
    <SetProperty Id="PixelPilotRollbackTasks" Value="${removeCommand}" Before="PixelPilotRollbackTasks" Sequence="execute"/>
    <CustomAction Id="PixelPilotRollbackTasks" BinaryRef="Wix4UtilCA_$(sys.BUILDARCHSHORT)" DllEntry="WixQuietExec" Execute="rollback" Impersonate="no" Return="ignore"/>
    <SetProperty Id="PixelPilotInstallTasks" Value="${installCommand}" Before="PixelPilotInstallTasks" Sequence="execute"/>
    <CustomAction Id="PixelPilotInstallTasks" BinaryRef="Wix4UtilCA_$(sys.BUILDARCHSHORT)" DllEntry="WixQuietExec" Execute="deferred" Impersonate="no" Return="check"/>
    <SetProperty Id="PixelPilotRemoveTasks" Value="${removeCommand}" Before="PixelPilotRemoveTasks" Sequence="execute"/>
    <CustomAction Id="PixelPilotRemoveTasks" BinaryRef="Wix4UtilCA_$(sys.BUILDARCHSHORT)" DllEntry="WixQuietExec" Execute="deferred" Impersonate="no" Return="ignore"/>
    <InstallExecuteSequence>
      <Custom Action="PixelPilotRollbackTasks" Before="PixelPilotInstallTasks">NOT Installed</Custom>
      <Custom Action="PixelPilotInstallTasks" After="InstallFiles">NOT Installed</Custom>
      <Custom Action="PixelPilotRemoveTasks" Before="RemoveFiles">REMOVE="ALL"</Custom>
    </InstallExecuteSequence>
    ${MARKER_END}`;
}

exports.default = async function customizeMsiProject(projectFilePath) {
  let source = fs.readFileSync(projectFilePath, 'utf-8');
  source = stripExistingCustomization(source);

  const productNameMatch = source.match(/<Product\b[^>]* Name="([^"]+)"/);
  const iconIdMatch = source.match(/<Icon Id="([^"]+)"/);
  const productName = productNameMatch?.[1] || 'PixelPilot';
  const iconId = iconIdMatch?.[1] || null;

  source = source.replace(/\s*<Property Id="WIXUI_INSTALLDIR" Value="APPLICATIONFOLDER"\/>\s*/g, '\n');

  source = replaceOrThrow(
    source,
    /<UIRef Id="WixUI_InstallDir"\/>\s*<UI>[\s\S]*?<\/UI>/,
    '<UIRef Id="WixUI_FeatureTree"/>',
    'default MSI UI block',
  );

  source = replaceOrThrow(
    source,
    /<!-- Desktop link -->\s*(?:<Directory Id="DesktopFolder" Name="Desktop"\/>\s*)?<!-- Start menu link -->/,
    ['<!-- Desktop link -->', '      <Directory Id="DesktopFolder" Name="Desktop"/>', '', '      <!-- Start menu link -->'].join('\n'),
    'desktop directory block',
  );

  source = replaceOrThrow(
    source,
    /<Feature Id="ProductFeature" Absent="disallow">\s*<ComponentGroupRef Id="ProductComponents"\/>\s*<\/Feature>/,
    buildFeatureBlock(productName),
    'default product feature block',
  );

  source = replaceOrThrow(
    source,
    /(<ComponentGroup Id="ProductComponents" Directory="APPLICATIONFOLDER">[\s\S]*?<\/ComponentGroup>)/,
    `$1\n${buildShortcutComponentGroup(productName, iconId)}`,
    'product components block',
  );

  source = replaceOrThrow(
    source,
    /\s*<\/Product>\s*<\/Wix>\s*$/,
    `\n${buildCustomActionBlock()}\n\n  </Product>\n</Wix>\n`,
    'closing product block',
  );

  fs.writeFileSync(projectFilePath, source, 'utf-8');
};
