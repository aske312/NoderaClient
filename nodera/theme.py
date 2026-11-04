"""A continuous application canvas with a floating navigation dock."""

STYLE = """
QWidget { background: transparent; color: #e4eff2; font-family: 'Segoe UI'; font-size: 12px; }
QMainWindow, QDialog, QMessageBox { background: #091720; }
QLabel { background: transparent; }
QLabel:disabled { color: #496572; }
QWidget#titleBar { background: transparent; border: none; }
QFrame#windowFrame, QWidget#windowBody, QStackedWidget, QScrollArea { background: transparent; border: none; }
QLabel#windowTitle { color: #b5cbd3; font-size: 12px; font-weight: 600; }
QFrame#sidebar { background: #0c1b24; border: 1px solid #1b303c; border-radius: 16px; }
QToolButton#nav, QToolButton#menuToggle { background: transparent; border: none; border-radius: 8px; padding: 8px; color: #8da6b1; font-weight: 500; text-align: left; }
QToolButton#nav:hover, QToolButton#menuToggle:hover { background: #142b36; color: #e4eff2; }
QToolButton#nav:checked { background: #173943; color: #9ce6e0; }
QToolButton#nav:focus, QToolButton#menuToggle:focus { border: 1px solid #3b747b; }
QToolButton#menuToggle { color: #6e8c9b; }
QFrame#sidebar[collapsed="true"] { background: transparent; border: none; }
QToolButton#nav[circular="true"] { background: #0e252f; border: 1px solid #23414d; border-radius: 17px; padding: 0; }
QPushButton#networkTag { padding: 3px 8px; border-radius: 8px; background: #17303a; color: #a4bac4; font-size: 11px; }
QPushButton#networkTag[state="ok"] { background: #153b32; border-color: #376754; color: #8fdbc1; }
QPushButton#networkTag[state="error"] { background: #3b2b26; border-color: #735044; color: #e6b49d; }
QPushButton#forceRoute:checked { background: #214e45; border-color: #4f9a83; color: #c1f3df; }
QLabel#routeMode { color: #9ddbd2; padding: 7px 10px; background: #102b32; border-radius: 7px; }
QToolButton#nav[circular="true"]:checked { background: #21515a; border-color: #5ab8b5; }
QToolButton#nav[circular="true"]:hover { background: #1c414c; }
QToolButton#menuToggle { padding: 3px; border-radius: 8px; }
QLabel#title { font-size: 21px; font-weight: 650; color: #eaf3f5; margin-bottom: 2px; }
QLabel#dashboardProfile { font-size: 20px; font-weight: 650; color: #eaf3f5; }
QLabel#profileName { font-size: 14px; font-weight: 600; }
QLabel#heroState { font-size: 12px; font-weight: 600; color: #c7e1e4; }
QLabel#heroMode { color: #6e929f; font-size: 11px; }
QLabel#subtitle, QLabel#muted { color: #7e9aa8; font-size: 11px; }
QLabel#eyebrow { color: #638592; font-size: 10px; font-weight: 600; margin-bottom: 4px; }
QLabel#value { font-size: 20px; font-weight: 600; }
QLabel#status { font-size: 18px; font-weight: 650; }
QLabel#badge { background: #15332e; color: #69c8aa; border-radius: 8px; padding: 4px 8px; font-size: 11px; }
QFrame#card, QFrame#profileBanner { background: #0e202a; border: 1px solid #1b3440; border-radius: 14px; }
QWidget#applicationCard { background: #0e202a; border: 1px solid #1b3440; border-radius: 12px; }
QFrame#settingsCard { background: #0d222c; border: 1px solid #1d3a47; border-radius: 10px; }
QFrame#quotaRail { background: #0c1e28; border: none; border-radius: 14px; }
QLabel#quotaValue { font-size: 13px; font-weight: 600; color: #d8edf0; }
QLabel#railDetail { font-size: 10px; color: #5d808e; }
QLabel#statValue { font-size: 11px; font-weight: 600; color: #acc7ce; }
QLabel#sessionTime { font-family: 'Cascadia Mono', Consolas; font-size: 14px; color: #a1c7cd; }
QPushButton { background: #142c37; color: #9dbbc5; border: 1px solid #24424f; border-radius: 7px; padding: 6px 11px; font-weight: 600; }
QPushButton:hover { background: #1b3a45; border-color: #3b747b; color: #e3f3f4; }
QPushButton:disabled { color: #435f6d; background: #10232c; border-color: #1a3440; }
QPushButton#primary { background: #234f55; color: #c8f3ed; border: 1px solid #386e72; }
QPushButton#primary:hover { background: #2b6265; border-color: #67c8c2; }
QPushButton#primary:disabled { background: #15313b; border-color: #244650; color: #53747e; }
QPushButton#quietAction { background: transparent; border-color: #223b47; color: #88aab6; padding: 7px 11px; }
QPushButton#quietAction:hover { background: #142d39; color: #c7e9eb; }
QPushButton#dashboardIcon { background: transparent; border: 1px solid #223b47; border-radius: 8px; padding: 0; }
QPushButton#dashboardIcon:hover { background: #163642; border-color: #416c77; }
QPushButton#heroRoute { background: transparent; border: none; border-radius: 8px; color: #aad6da; padding: 4px 0px; text-align: left; font-weight: 500; }
QPushButton#heroRoute:hover { background: #15313d; color: #d5f3ee; }
QPushButton#addProfile { font-size: 24px; font-weight: 400; padding: 0; background: #14313d; border: 1px solid #38616e; border-radius: 10px; color: #9adbd7; }
QPushButton#addProfile:hover { background: #21505a; border-color: #69c8c2; }
QLineEdit, QSpinBox, QDoubleSpinBox, QTextEdit, QPlainTextEdit, QComboBox, QListWidget, QTableWidget { background: #091a24; border: 1px solid #1e3a47; border-radius: 7px; padding: 6px; selection-background-color: #25505c; }
QTabWidget::pane { border: none; background: transparent; top: 6px; }
QTabBar::tab { background: transparent; color: #7e9aa8; border: 1px solid transparent; border-radius: 8px; padding: 7px 12px; margin-right: 5px; margin-bottom: 6px; }
QTabBar::tab:selected { background: #173943; color: #c8f3ed; border-color: #305965; }
QTabBar::tab:hover { background: #142b36; color: #e4eff2; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus { border-color: #59b6b7; }
QLineEdit::placeholder { color: #547583; }
QSpinBox, QDoubleSpinBox { padding: 6px 22px 6px 10px; }
QSpinBox QLineEdit, QDoubleSpinBox QLineEdit { border: none; background: transparent; padding: 0; }
QSpinBox::up-button, QDoubleSpinBox::up-button { subcontrol-origin: border; subcontrol-position: top right; width: 20px; border: none; background: transparent; }
QSpinBox::down-button, QDoubleSpinBox::down-button { subcontrol-origin: border; subcontrol-position: bottom right; width: 20px; border: none; background: transparent; }
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow, QSpinBox::down-arrow, QDoubleSpinBox::down-arrow { image: none; }
QComboBox { min-height: 18px; padding: 6px 32px 6px 10px; color: #d6e8eb; }
QComboBox:hover { background: #102631; border-color: #365a68; }
QComboBox:on { background: #122e39; border-color: #59b6b7; }
QComboBox:disabled { background: #10212a; color: #435f6d; border-color: #1a3440; }
QComboBox::drop-down { subcontrol-origin: padding; subcontrol-position: top right; width: 30px; border: none; background: transparent; }
QComboBox::down-arrow { image: none; width: 0; height: 0; }
QComboBox#profilePicker { background: transparent; border: 1px solid transparent; font-size: 14px; font-weight: 600; padding: 4px 30px 4px 8px; }
QComboBox#profilePicker:hover { background: #122b36; border-color: #294956; }
QLabel#routeSummary { color: #96bcc6; font-size: 11px; padding: 2px 8px; }
QListWidget#subscriptionList { background: transparent; border: none; padding: 0; }
QListWidget#subscriptionList::item { background: #0d222c; border: 1px solid #1c3945; border-radius: 8px; padding: 9px 12px; margin-bottom: 5px; }
QListWidget#subscriptionList::item:selected { background: #173943; border-color: #417980; }
QListWidget#subscriptionList::item:hover { background: #142f3a; }
QListView#comboPopup { background: #10242e; color: #e4eff2; border: 1px solid #365663; border-radius: 7px; padding: 3px; outline: none; font-size: 12px; font-weight: 400; }
QFrame#comboPopupFrame { background: transparent; border: none; }
QCheckBox { spacing: 10px; }
QCheckBox::indicator { width: 17px; height: 17px; border-radius: 5px; border: 1px solid #33515e; background: #0a1d27; }
QCheckBox::indicator:checked { background: #69c8aa; border-color: #80dfc0; }
QListWidget::item { padding: 10px; border-bottom: 1px solid #1a3440; }
QListWidget::item:selected { background: #183942; color: #e4eff2; }
QListWidget#nodeCards { background: transparent; border: none; padding: 0; }
QListWidget#nodeCards::item { padding: 0; border: none; background: transparent; }
QTreeWidget#connectionBlocks { background: transparent; border: none; padding: 0; outline: none; }
QTreeWidget#connectionBlocks::item { background: transparent; border: none; padding: 0; }
QHeaderView::section { background: #102833; border: none; padding: 8px; color: #83a5b3; font-weight: 600; }
QTableWidget { gridline-color: #1b3a46; }
QTableWidget::item { padding: 4px; }
QMenu, QToolTip { background: #102733; padding: 6px; border: 1px solid #2b4956; color: #d6e8eb; }
QMenu::item { padding: 7px 20px; border-radius: 6px; }
QMenu::item:selected { background: #244c57; }
QScrollBar:vertical { background: transparent; width: 6px; }
QScrollBar::handle:vertical { background: #294956; min-height: 36px; border-radius: 3px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
"""
