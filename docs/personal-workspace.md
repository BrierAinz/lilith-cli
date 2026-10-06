# Lilith: personal workspace direction

Direction requested on 2026-09-07: a useful, professional personal agent with
Nordic, Souls and anime influences. The first delivery improves the existing
runtime's front door and session recovery.

## Identity

- Obsidian surfaces, ice-blue accents and aged-gold borders.
- Restrained runes; ordinary, discoverable command names.
- Spanish voice: warm, direct, observant, with occasional dry humor.
- No compulsory delegation or minimum number of agents.
- Bifröst stays hidden background infrastructure.

La Hoguera is the entry point. Sagas are the existing `/goal` objectives attached
to conversations. Memory and the tool arsenal use existing runtime capabilities.
These names do not introduce a second scheduler or a parallel state database.

## Delivered behavior

- `lilith setup`: interactive or flag-based compatible-endpoint configuration,
  preserved existing settings, raw environment references and rollback backups.
- `lilith persona --apply`: optional personal voice without changing the provider.
- `lilith home --root PATH`: sessions and objective states for one project.
- `lilith start --root PATH --resume ID`: check project identity before loading
  conversation history; load project configuration in the selected directory.
- Bare `lilith`: interactive Hoguera with search, project selection and session
  launch. Entrance fade and ambient runes honor a persistent reduced-motion toggle.
  Existing explicit flags
  retain the established chat path.
- Atomic history snapshots after model turns, including interrupted turns, and
  on exit. A snapshot is updated instead of creating one file per autosave.
- Windows launcher preserves the caller's working directory.

The project directory is working context, not a filesystem security sandbox.
Resume retains conversation and goal context; it does not automatically reexecute
tools or restart unfinished external jobs. Current provider configuration wins.
Legacy sessions without a recorded root use the existing interactive `/resume`.

## Acceptance evidence

`test_hearth.py` tests configuration, preservation of credentials as references,
backup recovery, project identity, failed snapshot replacement and a real
AgentSession / provider-wrapper / file-tool loop against a simulated HTTP endpoint.
`test_hearth_entry.py` exercises command dispatch and the no-argument entry point.
The normal CLI suite also covers the existing REPL and Textual IDE.
Before the interactive Hoguera addition, the complete CLI suite passed 2,105 tests
with 16 skips and four warnings from existing IDE/LSP async cleanup. After the
addition, the focused Hoguera, entry-point, persistence and theme suite passed 30
tests. These counts overlap and should not be added together.
`test_hearth_ui.py` drives the new screen at 120x40 and 80x30 cells, checks
keyboard resumption, project filtering and persistent reduced motion. Reproduce
synthetic SVG previews with `python scripts/preview_hearth.py --output PATH`.

One minimal diagnostic request to the already configured provider confirmed a
response during implementation. It does not qualify all models or establish a
reliability/latency guarantee. Native Windows optional LiteLLM installation failed
without an MSVC linker; the compatible-endpoint runtime and dev dependencies
installed from the lockfile without that optional extra.

## References and scope

[Hermes Agent](https://github.com/NousResearch/hermes-agent) is the reference for
integrated setup, provider choice and session continuity.
[Aether Agents](https://github.com/DarkArty07/Aether-Agents) is the reference for
explicit objectives and proportional execution; its current README describes a
beta stabilization boundary, not a fully qualified public release.
Inspected 2026-09-07; their code was not copied.

This delivery does not claim feature parity with either project. Dedicated anime
artwork, messaging integrations, scheduled autonomy, packaging for third-party
installation and broader live-provider qualification require their own acceptance
criteria. The terminal workspace is the first supported user journey.
