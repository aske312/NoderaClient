"""A continuous application canvas with a floating navigation dock."""


def prepare_fonts(app):
    """Load the same Windows fonts once for the client, installer and UI checks."""
    import os
    from pathlib import Path

    from PySide6.QtGui import QFontDatabase

    if os.name != "nt" or app.property("noderaFontsLoaded"):
        return
    fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    for name in ("segoeui.ttf", "seguisb.ttf"):
        QFontDatabase.addApplicationFont(str(fonts / name))
    app.setProperty("noderaFontsLoaded", True)


def configure_style(app):
    prepare_fonts(app)
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)


STYLE = """
QWidget { background: transparent; color: #e4eff2; font-family: 'Segoe UI'; font-size: 12px; }
QMainWindow, QDialog, QMessageBox { background: #091720; }
QDialog#proOptions { background: transparent; }
QDialog#appModal { background: transparent; }
QWidget#modalBody { background: transparent; }
QLabel#modalHeading { font-size: 16px; font-weight: 600; color: #dcece8; }
QDialog#appModal QPushButton { padding: 4px 10px; }
QDialog#appModal QLineEdit { padding: 4px 7px; }
QDialog#appModal QComboBox { padding: 4px 28px 4px 8px; min-height: 16px; }
QLabel#modalDescription { color: #a5bdc6; line-height: 1.4; }
QPushButton#dangerAction { background: #422d31; border: 1px solid #78505a; color: #e7b7bf; }
QPushButton#dangerAction:hover { background: #56363e; border-color: #a46976; }
QPushButton#proOptionCard { background: #0d222c; border: 1px solid #1d3a47; border-radius: 10px; text-align: left; }
QPushButton#proOptionCard:hover, QPushButton#proOptionCard:focus { background: #15313b; border-color: #417980; }
QPushButton#protocolPicker { text-align: left; font-weight: 400; }
QPushButton#protocolPicker::menu-indicator { image: none; }
QListWidget#protocolChoices { background: transparent; border: none; padding: 0; }
QListWidget#protocolChoices::item { padding: 3px 4px; border: none; border-radius: 5px; }
QListWidget#protocolChoices::indicator { width: 15px; height: 15px; border: 1px solid #33515e; border-radius: 4px; background: #0a1d27; }
QListWidget#protocolChoices::indicator:checked { background: #69c8aa; border-color: #80dfc0; }
QLabel { background: transparent; }
QLabel#subscriptionWarning { color: #edbba4; }
QWidget#dashboardFeedback QLabel { font-size: 11px; }
QLabel:disabled { color: #496572; }
QWidget#titleBar { background: transparent; border: none; }
QFrame#windowFrame, QWidget#windowBody, QStackedWidget, QScrollArea { background: transparent; border: none; }
QLabel#windowTitle { color: #b5cbd3; font-size: 12px; font-weight: 600; }
QWidget#titleBar[compact="true"] QLabel#windowTitle { font-size: 14px; color: #d5e6e7; }
QFrame#navigationDock { background: #0d222c; border: 1px solid #28424d; border-radius: 16px; }
QToolButton#dockTab { background: transparent; border: 1px solid transparent; border-radius: 9px; padding: 4px 7px; color: #8da6b1; font-weight: 500; }
QToolButton#dockTab:hover { background: #17343e; color: #e4eff2; }
QToolButton#dockTab:checked { background: #20463f; border-color: #326355; color: #b4ecd6; }
QToolButton#dockTab:focus { border-color: #3b747b; }
QToolButton#dockTab[tools="true"] { padding-right: 18px; }
QFrame#dockDivider { background: #28424d; border: none; }
QFrame#navigationPalette { background: #10242e; border: 1px solid #304c57; border-radius: 10px; }
QPushButton#navigationEntry { background: transparent; border: 1px solid transparent; border-radius: 7px; padding: 0; text-align: left; }
QPushButton#navigationEntry:hover, QPushButton#navigationEntry:focus { background: #1a3640; border-color: #355763; }
QPushButton#navigationEntry:checked { background: #1a3c39; border-color: #315c51; }
QLabel#navigationTitle { font-size: 12px; font-weight: 500; color: #d6e8ec; }
QPushButton#networkTag { padding: 3px 8px; border-radius: 8px; background: #17303a; color: #a4bac4; font-size: 11px; }
QPushButton#networkTag[state="ok"] { background: #153b32; border-color: #376754; color: #8fdbc1; }
QPushButton#networkTag[state="error"] { background: #3b2b26; border-color: #735044; color: #e6b49d; }
QPushButton#connectionStatus { background: #142d37; border: 1px solid #2b4855; border-radius: 12px; color: #95b2bd; font-size: 11px; font-weight: 600; padding: 4px 12px; min-height: 16px; }
QPushButton#connectionStatus:disabled { background: #142d37; border-color: #2b4855; color: #95b2bd; }
QPushButton#connectionStatus[state="ok"] { background: #153b32; border-color: #376754; color: #9ce2c8; }
QPushButton#connectionStatus[state="error"] { background: #3b2b26; border-color: #735044; color: #edbba4; }
QPushButton#connectionStatus[state="checking"] { background: #17343f; border-color: #376471; color: #a9d6df; }
QPushButton#connectionStatus:hover:enabled { border-color: #63a5a1; }
QFrame#serverChecks { background: #10232d; border: 1px solid #203b47; border-radius: 9px; }
QFrame#routeHop { background: #10252e; border: 1px solid #26424e; border-radius: 10px; }
QLabel#hopNumber { background: #1c3c40; color: #9cdbcc; border-radius: 12px; font-size: 11px; font-weight: 600; }
QToolButton#hopAction { background: transparent; color: #98b6c1; border: none; border-radius: 5px; }
QToolButton#hopAction:hover { background: #21404c; }
QToolButton#hopAction:disabled { color: #425d68; }
QLabel#routeEditorError { color: #e6b49d; }
QPushButton#forceRoute:checked { background: #214e45; border-color: #4f9a83; color: #c1f3df; }
QLabel#routeMode { color: #9ddbd2; padding: 7px 10px; background: #102b32; border-radius: 7px; }
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
QFrame#aboutDivider { background: #1b3440; border: none; }
QToolButton#aboutDetails { background: transparent; border: none; color: #8da6b1; padding: 0; text-align: left; }
QToolButton#aboutDetails:hover { color: #c8f3ed; }
QProgressBar#aboutProgress { background: #16333d; border: none; border-radius: 1px; }
QProgressBar#aboutProgress::chunk { background: #69c8b8; border-radius: 1px; }
QFrame#settingsCard { background: #0d222c; border: 1px solid #1d3a47; border-radius: 10px; }
QWidget#settingsPage QComboBox { padding: 4px 28px 4px 8px; min-height: 16px; font-size: 11px; }
QWidget#settingsPage QSpinBox, QWidget#settingsPage QDoubleSpinBox { padding: 4px 22px 4px 8px; font-size: 11px; }
QWidget#settingsPage QLineEdit { padding: 4px 8px; font-size: 11px; }
QWidget#settingsPage QPushButton { padding: 4px 10px; font-size: 11px; }
QWidget#settingRow:hover { background: #102832; border-radius: 6px; }
QTabWidget#compactSettings QTabBar::tab { padding: 6px 10px; margin-right: 3px; margin-bottom: 3px; }
QFrame#subscriptionDetail { background: #0d222c; border: 1px solid #203b47; border-radius: 12px; }
QLabel#subscriptionName { font-size: 15px; font-weight: 600; color: #e2f0f2; }
QLabel#summaryValue { font-size: 14px; font-weight: 600; color: #c8e7e5; }
QLabel#proBadge { background: #173e37; color: #8cddbd; border: 1px solid #306a56; border-radius: 6px; padding: 2px 6px; font-size: 10px; font-weight: 700; }
QFrame#applicationUpdates { background: #0d222c; border: 1px solid #1d3a47; border-radius: 10px; }
QPushButton#dangerAction { background: transparent; border-color: #493839; color: #c7908d; }
QPushButton#dangerAction:hover { background: #38272b; border-color: #80504f; }
QFrame#dashboardHeader, QFrame#dashboardDock { background: transparent; border: none; }
QFrame#dashboardHeader QComboBox#profilePicker { background: rgba(13, 35, 45, 210); border: 1px solid rgba(61, 101, 116, 155); border-radius: 12px; font-size: 13px; }
QFrame#dashboardHeader QPushButton#dashboardIcon { background: rgba(13, 35, 45, 190); border: 1px solid rgba(61, 101, 116, 145); border-radius: 15px; }
QFrame#dashboardHeader QPushButton#dashboardOptions { background: rgba(13, 35, 45, 190); border: 1px solid rgba(61, 101, 116, 145); border-radius: 15px; padding: 3px 12px; }
QFrame#dashboardInfoCard { background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 rgba(18, 43, 55, 175), stop:1 rgba(9, 25, 35, 155)); border: 1px solid rgba(66, 109, 122, 110); border-radius: 16px; }
QFrame#dashboardDock QPushButton#connectionStatus { background: rgba(20, 45, 55, 210); border: 1px solid #2b4855; border-radius: 10px; font-size: 11px; color: #9bbbc5; padding: 2px 8px; }
QFrame#dashboardDock QPushButton#connectionStatus[state="ok"] { background: rgba(21, 59, 50, 220); border-color: #376754; color: #9ce2c8; }
QFrame#dashboardDock QPushButton#connectionStatus[state="error"] { background: rgba(59, 43, 38, 220); border-color: #735044; color: #edbba4; }
QFrame#dashboardDock QPushButton#connectionStatus[state="slow"], QFrame#dashboardDock QPushButton#connectionStatus[state="unstable"] { background: rgba(54, 44, 29, 220); border-color: #665739; color: #d3c095; }
QFrame#dashboardDock QPushButton#connectionStatus[state="checking"] { background: rgba(23, 52, 63, 220); border-color: #376471; color: #a9d6df; }
QFrame#dashboardDock QPushButton#connectionStatus:hover:enabled { border-color: #63a5a1; }
QFrame#quotaRail { background: transparent; border: none; }
QLabel#dashboardCaption { color: #88aebc; font-size: 10px; font-weight: 600; }
QLabel#dashboardSummary { color: #7e9da9; font-size: 11px; }
QLabel#dashboardMetricCaption { color: #7294a2; font-size: 10px; }
QLabel#dashboardMetric { color: #d2e9e9; font-size: 14px; font-weight: 600; }
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
QTableWidget#routeAddresses { background: #0c1e28; alternate-background-color: #10252e; border: none; border-radius: 0; selection-background-color: #20423f; }
QTableWidget#routeAddresses::item { padding: 7px 9px; border: none; border-bottom: 1px solid #1c3540; }
QTableWidget#routeAddresses::item:hover { background: #18323d; }
QTableWidget#routeAddresses::item:selected { background: #20423f; color: #d7f0e6; }
QTableWidget#routeAddresses QHeaderView::section { background: #10252e; border: none; border-bottom: 1px solid #31505a; padding: 9px; color: #8caab5; }
QMenu, QToolTip { background: #102733; padding: 6px; border: 1px solid #2b4956; color: #d6e8eb; }
QMenu::item { padding: 7px 20px; border-radius: 6px; }
QMenu::item:selected { background: #244c57; }
QScrollBar:vertical { background: transparent; width: 6px; }
QScrollBar::handle:vertical { background: #294956; min-height: 36px; border-radius: 3px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
"""
