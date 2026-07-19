from backend.proxy.contracts import ProviderName

HTTP_FACADE_METHODS = frozenset(
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

PROVIDER_METHODS: dict[ProviderName, frozenset[str]] = {
    ProviderName.HTTP: HTTP_FACADE_METHODS,
}
