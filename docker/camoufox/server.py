from camoufox.server import launch_server

if __name__ == "__main__":
    launch_server(
        headless=True,
        host="0.0.0.0",
        port=1234,
        ws_path="harbor",
    )
