"""Deterministic challenge origin for local Compose E2E tests; never deployed with the API."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ARTICLE = (
    "<main><h1>Local resolution demonstration</h1>"
    + "".join(
        f"<p>Section {i}: This is the complete article obtained "
        "after the browser executes the challenge.</p>"
        for i in range(12)
    )
    + "</main>"
)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/pixel.svg":
            body = b'<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"></svg>'
            self.send_response(200)
            self.send_header("Content-Type", "image/svg+xml")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/article/"):
            body = (
                "<html><head><title>Article</title></head><body>" + ARTICLE + "</body></html>"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        native = "StolosioBot" not in self.headers.get("User-Agent", "")
        script = ""
        if native and self.path.startswith("/clears/"):
            script = (
                "<script>setTimeout(() => {document.title='Article';document.body.innerHTML="
                + json.dumps(ARTICLE)
                + ";}, 300);</script>"
            )
        elif native and self.path.startswith("/progress/"):
            script = (
                '<script id="anubis_challenge" type="application/json">{}</script>'
                '<progress id="progress" max="12" value="0"></progress>'
                "<script>let n=0;const timer=setInterval(()=>{"
                'document.querySelector("progress").value=++n;if(n===12){clearInterval(timer);'
                "document.title='Article';document.body.innerHTML="
                + json.dumps(ARTICLE)
                + ";}},1000);</script>"
            )
        elif native and self.path.startswith("/resources/"):
            script = (
                '<img src="/pixel.svg" onload="document.title=\'Article\';document.body.innerHTML='
            )
            script += json.dumps(ARTICLE).replace('"', "&quot;") + ';">'
        body = (
            "<html><head><title>Just a moment...</title></head><body>"
            "Performing security verification" + script + "</body></html>"
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 80), Handler).serve_forever()
