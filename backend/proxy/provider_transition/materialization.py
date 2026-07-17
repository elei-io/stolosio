from collections.abc import Iterable


class CdpReplayMaterializationStrategy:
    """Declares which synthetic HTTP state can be reconstructed on a provider."""

    replay_safe_methods = frozenset(
        {
            "Browser.getVersion",
            "Browser.getWindowForTarget",
            "Browser.setDownloadBehavior",
            "Browser.setWindowBounds",
            "Emulation.setDeviceMetricsOverride",
            "Emulation.setEmulatedMedia",
            "Emulation.setFocusEmulationEnabled",
            "Emulation.setScriptExecutionDisabled",
            "Log.enable",
            "Network.enable",
            "Page.addScriptToEvaluateOnNewDocument",
            "Page.createIsolatedWorld",
            "Page.enable",
            "Page.getFrameTree",
            "Page.navigate",
            "Page.setLifecycleEventsEnabled",
            "Runtime.callFunctionOn",
            "Runtime.enable",
            "Runtime.evaluate",
            "Runtime.releaseObject",
            "Runtime.runIfWaitingForDebugger",
            "Target.createBrowserContext",
            "Target.createTarget",
            "Target.disposeBrowserContext",
            "Target.getTargetInfo",
            "Target.setAutoAttach",
        }
    )

    def can_materialize(self, methods: Iterable[str]) -> bool:
        return all(method in self.replay_safe_methods for method in methods)
