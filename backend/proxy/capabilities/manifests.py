from backend.proxy.contracts import ProviderName

EXAMPLE_BASELINE = frozenset(
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

PROVIDER_METHODS: dict[ProviderName, frozenset[str]] = {
    ProviderName.HTTP: EXAMPLE_BASELINE,
    ProviderName.CHROMIUM: EXAMPLE_BASELINE,
    ProviderName.BROWSERLESS: EXAMPLE_BASELINE | {"Page.setFontFamilies"},
    ProviderName.LIGHTPANDA: EXAMPLE_BASELINE
    | {
        "Emulation.setScriptExecutionDisabled",
        "Target.closeTarget",
    },
    ProviderName.CAMOUFOX: EXAMPLE_BASELINE,
}
