# Contributing Workflows

The workflow library lives in a single JSON file at `web/src/data/workflows.json`. Adding or improving a workflow is one of the easiest ways to contribute to the project - no backend code required.

## Workflow structure

Each workflow is a JSON object with these fields:

```json
{
  "id": "unique-kebab-case-id",
  "name": "Human-Readable Name",
  "description": "One-sentence summary shown on the card",
  "category": "genomics",
  "icon": "Dna",
  "prompt": "Detailed instructions with {placeholder} syntax for user variables",
  "suggestedSkills": ["scanpy", "scientific-visualization"],
  "placeholders": [
    { "key": "placeholder", "label": "What to ask the user", "required": true }
  ],
  "requiresFiles": true
}
```

Set `requiresFiles` to `true` when the workflow needs user-uploaded data (datasets, manuscripts, images, etc.). These workflows display a "Files" badge on the card and show an upload button in the launch dialog so users can add files to the sandbox before running.

## How to add a workflow

1. Open `web/src/data/workflows.json`.
2. Add your workflow object anywhere in the array (it will be grouped by `category` automatically).
3. Pick a `category` from the existing 22 disciplines:

   `paper`, `visual`, `data`, `literature`, `grants`, `scicomm`, `genomics`, `proteomics`, `cellbio`, `chemistry`, `drugdiscovery`, `physics`, `materials`, `clinical`, `neuro`, `ecology`, `finance`, `social`, `math`, `ml`, `engineering`, `astro`

   Or propose a new one.
4. Choose an `icon` name from [Lucide Icons](https://lucide.dev/icons/) (PascalCase, no "Icon" suffix - e.g. `FlaskConical`, `Brain`, `Dna`). If the icon isn't already imported in `workflows-panel.tsx`, add it there too.
5. List `suggestedSkills` from the [K-Dense scientific skills](https://github.com/K-Dense-AI/scientific-agent-skills) - these are passed to the agent so it knows which tools to load. Only use skill IDs that exist in the repo.
6. Use `{placeholder}` syntax in the prompt for any variable the user should fill in, and add a matching entry in `placeholders`.

## Tips for high-quality workflows

- Write prompts with **numbered steps** so the agent follows a clear procedure.
- Include **2-5 suggested skills** - enough to be helpful, not so many that they dilute focus.
- Mark placeholders as `"required": true` only when the workflow genuinely can't run without them.
- Keep descriptions under ~120 characters so they display well on the card.

Submit your addition as a pull request. We review and merge workflow contributions quickly.
