# Browser Automation

Kady and the expert agent can drive a real browser through the [browser-use CLI](https://docs.browser-use.com/open-source/browser-use-cli). When enabled, MCP tools for navigating pages, clicking elements, filling forms, taking screenshots, and evaluating JavaScript are injected into both the orchestrator and the expert.

No extra API keys are required - everything runs locally.

## How to enable it

You can toggle browser automation in two places:

- **The Browser chip in the input bar** (next to Compute and Skills). Quick enable/disable and a headed/headless toggle, per project.
- **Settings → Browser.** Same controls, plus an optional **Chrome profile** name if you want browser-use to connect to a real Chrome profile (preserving your logins and cookies) instead of using its own managed Chromium.

## First-run setup

On first launch, `prep_sandbox.py` runs `uvx browser-use install` once to download Chromium. This only happens on first launch - subsequent starts skip it via a marker file (`.venv/.browser-use-installed`).

## How it's stored

The browser-use configuration is saved **per project** in `projects/<project-id>/browser_use.json`:

```json
{
  "enabled": true,
  "headed": false,
  "profile": null,
  "session": null
}
```

The backend hot-reloads the MCP connection on every turn, so toggling the chip takes effect on the next message - no restart required.
