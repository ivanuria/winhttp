import comtypes
import comtypes.client
import urllib.request, urllib.parse
import json as js
import ctypes
import html
import os
import hashlib
import random
import shelve
import time
import win32gui
import webbrowser
import dbm
from comtypes import COMError
from functools import partial
from html.parser import HTMLParser
from uuid import UUID, uuid4

IID_IStream = ctypes.c_buffer(UUID("{0000000C-0000-0000-C000-000000000046}").bytes_le)

LOCALPATH = os.path.join(os.environ["LOCALAPPDATA"], "zashel", "winhttp")

class NotAuthorised(Exception):
    pass

class Requests:
    def __init__(self):
        self.proxy = urllib.request.getproxies()["http"]
        self._req = comtypes.client.CreateObject("Msxml2.XMLHTTP")
        self.get = partial(self.request, "GET")
        self.post = partial(self.request, "POST")
        self.put = partial(self.request, "PUT")
        self.delete = partial(self.request, "DELETE")
        self.patch = partial(self.request, "PATCH")
        self.head = partial(self.request, "HEAD")
        self._parser = HTMLParser
        self.token = None
        self.scopes = None
        self.secrets = None
        self.openidtoken = None
        self.uuid = None

    @property
    def headers(self):
        headers = self._req.GetAllResponseHeaders().strip("\r\n")
        headers = headers.split("\r\n")
        final = dict()
        for item in headers:
            data = item.split(":")
            key = data[0]
            value = data[1:]
            if isinstance(value, list):
                value = ":".join(value)
            key = key.strip()
            value = value.strip()
            final[key] = value
        return final

    @property
    def status_code(self):
        try:
            return self._req.status
        except COMError:
            return None

    @property
    def body(self):
        try:
            return self._req.responseBody
        except COMError:
            return None

    @property
    def text(self):
        try:
            return self._req.responseText
        except COMError:
            return None

    @property
    def stream(self):
        try:
            return self._req.ResponseStream.QueryInterface(comtypes.IUnknown)
        except COMError:
            return None

    @property
    def url(self):
        try:
            return self._req.Option(1)
        except COMError:
            return None

    def request(self, method, url, *, data=None, json=None, headers=None, get=None):
        requested = ([method, url], {"data": data, "json": json, "headers": headers, "get": get})
        if get is None:
            get = dict()
        if self.token is not None:
            if self.token.endswith(".json"):
                with open(self.token, "r") as f:
                    get["access_token"] = js.loads(f.read().strip())["access_token"]
            else:
                import shelve
                try:
                    shelf = shelve.open(self.token)
                except dbm.error:
                    self.oauth2_logout()
                    return self.refresh_token(requested)
                else:
                    try:
                        get["access_token"] = shelf["access_token"]
                    except KeyError:
                        pass
                    shelf.close()
        if get != dict():
            if "?" not in url:
                url = url + "?"
            elif not url.endswith("&") and not url.endswith("?"):
                url = url + "&"
            url = url+urllib.parse.urlencode(get)
        self._req.Open(method, url, False)
        self._req.SetRequestHeader("User_Agent",
                                   "Mozilla/5.0 (Windows NT 6.1; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/60.0.3112.101 Safari/537.36")
        self._req.SetRequestHeader("Accept",
                                   "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8")
        self._req.SetRequestHeader("Accept_Encoding",
                                   "gzip, deflate, br")
        self._req.SetRequestHeader("Upgrade_Insecure_Requests",
                                   "1")
        self._req.SetRequestHeader("Connection",
                                   "keep-alive")
        if headers is not None:
            for key in headers:
                self._req.SetRequestHeader(key, headers[key])
        if json is not None:
            json = js.dumps(json)
            self._req.SetRequestHeader("Content-type", "application/json;charset=utf-8")
            self._req.send(str(json))
        elif data is not None:
            if isinstance(data, dict):
                #data = urllib.parse.urlencode(data)
                final = "&".join(["=".join([key, html.escape(data[key])]) for key in data])
                data = final
            if headers is None or (headers is not None and not "Content-type" in headers):
                self._req.SetRequestHeader("Content-type", "application/x-www-form-urlencoded;charset=utf-8")
            self._req.send(str(data))
        else:
            self._req.send()
        if self.token is not None and self.status_code in [401]:
            return self.refresh_token(requested)
        return self.text

    def refresh_token(self, requested):
        shelf = shelve.open(self.token)
        try:
            token = shelf["refresh_token"]
        except KeyError:
            if os.path.exists(self.token):
                os.remove(self.token)
            ok = self.oauth2(self.scopes, self.secret_file)
            if ok is not None:
                self.request(*requested[0], **requested[1])
                return
        else:
            shelf.close()
            self.post(self.secrets["token_uri"], data={"client_id": self.secrets["client_id"],
                                                       "client_secret": self.secrets["client_secret"],
                                                       "refresh_token": token,
                                                       "grant_type": "refresh_token"})
            shelf = shelve.open(self.token)
            shelf.update(js.loads(self.text))
            shelf.close()
        self.request(*requested[0], **requested[1])
        if self.token is not None and self.status_code in [401]:
            self.token = None
            ok = self.oauth2(self.scopes, self.secret_file)
            if ok is not None:
                return self.request(*requested[0], **requested[1])
        else:
            return js.loads(self.text)
        if os.path.exists(self.token):
            os.remove(self.token)

    def oauth2(self, scopes, json_file, *, token=None):
        assert os.path.exists(json_file)
        if isinstance(scopes, str):
            scopes = [scopes]
        with open(json_file) as json:
            data = js.load(json)
        if "installed" in data:

            data = data["installed"]
        if "redirect_uris" in data:
            data["redirect_uri"] = [uri for uri in data["redirect_uris"] if "oob" in uri][0]
            data["redirect_uri"] = data["redirect_uri"]+":auto"
        keys = ["client_id", "project_id", "auth_uri", "token_uri", "auth_provider_x509_cert_url", "client_secret"]
        assert(all([item in data for item in keys]))
        self.scopes = scopes
        self.secret_file = json_file
        self.secrets = data
        if token is None:
            token = os.path.join(LOCALPATH, data["client_id"])
        self.token = token
        if not os.path.exists(self.token):
            self.state = hashlib.sha256(os.urandom(1024)).hexdigest()
            self.nonce = ''.join([str(random.randint(0, 9)) for i in range(8)])
            auth_data = {"response_type":"code",
                         "client_id": data["client_id"],
                         "scope": " ".join(["openid", "email"]+scopes),
                         "redirect_uri":data["redirect_uri"],
                         "state": self.state,
                         "nonce": self.nonce,
                         "hd": "*"
                         }
            opened = list()
            def get_opened(handle, opened):
                name = win32gui.GetWindowText(handle)
                if name.startswith("Success ") and "hd=transcom.com" in name:
                    opened.append(name)
            win32gui.EnumWindows(get_opened, opened)

            webbrowser.open("{}?{}".format(data["auth_uri"], "&".join(
                        ["=".join((key, auth_data[key].replace(" ", "%20"))) for key in auth_data])), 2)
            received = dict()
            def receive(handle, received):
                name = win32gui.GetWindowText(handle)
                if name not in opened and name.startswith("Success ") and "hd=transcom.com" in name:
                    received.update(urllib.parse.parse_qs(name.split(" ")[1]))
                    for key in received:
                        received[key] = received[key][0]
            while received == dict():
                win32gui.EnumWindows(receive, received)
                time.sleep(0.5)
            if received["state"] == self.state:
                self.post(data["token_uri"], data={"code": received["code"],
                                                   "client_id": data["client_id"],
                                                   "client_secret": data["client_secret"],
                                                   "redirect_uri": data["redirect_uri"],
                                                   "grant_type": "authorization_code"})
                token_data = js.loads(self.text)
                shelf = shelve.open(self.token)
                shelf.update(token_data)
                shelf.close()
                uuid = self.uuid and self.uuid or uuid4().hex
                with open(self.token, "w") as f:
                    f.write(uuid)
            else:
                return None
        with open(self.token, "r") as f:
            self.uuid = f.read().strip("\r\n ")
        return True

    def oauth2_logout(self):
        ls = os.listdir(os.path.split(self.token)[0])
        for item in ls:
            if os.path.split(self.token)[1] in item:
                os.remove(os.path.join(os.path.split(self.token)[0], item))