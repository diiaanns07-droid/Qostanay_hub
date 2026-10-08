# ADAL-EXAM-WEB — checkpoint 1

Branch: `codex/adal-exam-web`; baseline `308b8ac`. Parent coordinates integration; no push requested.
Scope: website URL mode in the Windows Electron student app. Never an OS-wide browser/app/firewall claim.

Implemented first checkpoint: strict URL policy; isolated ephemeral exam Session/WebContentsView without preload; request/navigation/redirect checks; deny downloads/popups/permissions; synchronous destruction on lock/pause/end/backend loss; private desktop viewport/status bridge helper. Main/preload/renderer wiring and actual Electron fixture validation still in progress.

Policy: exact HTTP(S) origin + path; terminal `/*` subtree only; no host wildcard, credentials, ambiguous encoded separators. First allowlisted URL is landing URL. Every auth/CDN/resource endpoint must be listed explicitly. Exact pattern query constrains query; absent query allows query parameters. Pop-up authentication is deliberately unsupported; same-window authentication supported. Native application mode reports unsupported.

Validation at checkpoint: initial unit suite and TypeScript checks about to run; no real devices/native hooks activated. Dependencies copied read-only from coordinator's existing installation into this worktree.

Next: integrate private status/viewport into dedicated external exam screen, then real Electron local HTTP fixtures (allow/block/resource/redirect/download/permissions/lock lifecycle). Parent will merge shared main.ts lock/audio helper wiring.
