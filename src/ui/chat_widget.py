from PySide6.QtWidgets import QWidget, QVBoxLayout, QTextEdit, QLineEdit, QPushButton

class ChatWidget(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout()
        
        self.chat_display = QTextEdit()
        self.chat_display.setReadOnly(True)
        layout.addWidget(self.chat_display)
        
        self.input_field = QLineEdit()
        layout.addWidget(self.input_field)
        
        self.send_btn = QPushButton("Send")
        layout.addWidget(self.send_btn)
        
        self.setLayout(layout)
