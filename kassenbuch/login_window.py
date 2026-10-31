"""
Desktop login window (PySide6): shows Lidl's login page and catches the redirect to
com.lidlplus.app://callback itself - no DevTools, no copying.
"""

import sys

from kassenbuch import auth


def login_window(account: str, country: str = "de") -> bool:
    """Open the login window. Returns True if the account was saved."""
    try:
        import shiboken6
        from PySide6.QtCore import QTimer, QUrl
        from PySide6.QtWidgets import QApplication, QMessageBox
        from PySide6.QtWebEngineCore import (
            QWebEnginePage,
            QWebEngineProfile,
            QWebEngineUrlRequestJob,
            QWebEngineUrlScheme,
            QWebEngineUrlSchemeHandler,
        )
        from PySide6.QtWebEngineWidgets import QWebEngineView
    except ImportError:
        raise auth.AuthError("Für das Login-Fenster wird PySide6 benötigt: pip install 'kassenbuch[login]' "
                             "(ohne Fenster: 'kassenbuch login-start' und 'login-finish')") from None

    scheme = auth.REDIRECT_URI.split(":")[0].encode()
    # Custom schemes must be registered before the QApplication exists
    QWebEngineUrlScheme.registerScheme(QWebEngineUrlScheme(scheme))
    app = QApplication(sys.argv[:1])
    saved = False

    def complete(callback_url: str) -> None:
        nonlocal saved
        try:
            auth.finish_login(account, callback_url)
            saved = True
            QMessageBox.information(view, "Lidl Login", f"Konto '{account}' ist gespeichert.")
        except auth.AuthError as e:
            QMessageBox.critical(view, "Lidl Login", str(e))
        app.quit()

    class CallbackHandler(QWebEngineUrlSchemeHandler):
        def requestStarted(self, job):
            callback_url = job.requestUrl().toString()
            job.fail(QWebEngineUrlRequestJob.Error.RequestAborted)
            # Leave the network callback before showing a dialog
            QTimer.singleShot(0, lambda: complete(callback_url))

    # Off-the-record profile: no cookies from earlier logins, so Lidl can't silently reuse another account
    profile = QWebEngineProfile()
    handler = CallbackHandler(profile)
    profile.installUrlSchemeHandler(scheme, handler)

    view = QWebEngineView()
    view.setPage(QWebEnginePage(profile, view))
    view.setWindowTitle(f"Lidl Login – {account}")
    view.resize(480, 800)
    view.load(QUrl(auth.start_login(account, country)))
    view.show()
    app.exec()
    # The page has to go before its profile, otherwise Qt complains at exit
    shiboken6.delete(view)
    return saved
