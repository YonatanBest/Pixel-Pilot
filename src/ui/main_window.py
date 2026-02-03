from PySide6.QtWidgets import QMainWindow, QWidget, QVBoxLayout, QLabel

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Tolin AI")
        self.resize(800, 600)
        
        container = QWidget()
        layout = QVBoxLayout()
        layout.addWidget(QLabel("Tolin AI Assistant"))
        container.setLayout(layout)
        
        self.setCentralWidget(container)
