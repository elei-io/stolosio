import camoufox.server

_launch_options = camoufox.server.launch_options


def launch_options(**kwargs):
    options = _launch_options(**kwargs)
    return {key: value for key, value in options.items() if value is not None}


if __name__ == "__main__":
    camoufox.server.launch_options = launch_options
    camoufox.server.launch_server(
        headless=True,
        host="0.0.0.0",
        port=1234,
        ws_path="harbor",
    )
