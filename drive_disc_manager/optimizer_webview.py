"""Lazy Qt WebEngine dependency used only by the interactive optimizer page."""
from PySide6.QtCore import Qt, QUrl
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineUrlRequestInterceptor
from PySide6.QtWebEngineWidgets import QWebEngineView


class LocalRequests(QWebEngineUrlRequestInterceptor):
    def __init__(self, url, parent):
        super().__init__(parent)
        self.origin = QUrl(url)

    def interceptRequest(self, info):
        target = info.requestUrl()
        if target.scheme() in ('data', 'blob', 'about'):
            return
        same = (target.scheme() == self.origin.scheme() and target.host() == self.origin.host()
                and target.port() == self.origin.port())
        if not same:
            info.block(True)


def local_view(url, parent):
    view = QWebEngineView(parent)
    view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
    profile = QWebEngineProfile(view)
    interceptor = LocalRequests(url, profile)
    profile.setUrlRequestInterceptor(interceptor)
    # The page must be destroyed before its private profile.
    page = QWebEnginePage(profile, profile)
    view.setPage(page)
    view.local_profile = profile
    view.local_requests = interceptor
    return view
