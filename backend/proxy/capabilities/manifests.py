from backend.proxy.contracts import ProviderName

NATIVE_BROWSER_BASELINE = frozenset(
    {
        "Browser.getVersion",
        "Browser.getWindowForTarget",
        "Browser.setDownloadBehavior",
        "Browser.setWindowBounds",
        "DOM.getContentQuads",
        "DOM.scrollIntoViewIfNeeded",
        "Emulation.setDeviceMetricsOverride",
        "Emulation.setEmulatedMedia",
        "Emulation.setFocusEmulationEnabled",
        "Input.dispatchMouseEvent",
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
        "Target.disposeBrowserContext",
        "Target.createTarget",
        "Target.getTargetInfo",
        "Target.setAutoAttach",
    }
)

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
    ProviderName.CHROMIUM: NATIVE_BROWSER_BASELINE,
    ProviderName.BROWSERLESS: NATIVE_BROWSER_BASELINE | {"Page.setFontFamilies"},
    ProviderName.LIGHTPANDA: NATIVE_BROWSER_BASELINE
    | {
        "Emulation.setScriptExecutionDisabled",
        "Target.closeTarget",
    },
    ProviderName.CAMOUFOX: NATIVE_BROWSER_BASELINE,
}
