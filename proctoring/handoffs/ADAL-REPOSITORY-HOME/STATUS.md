# Repository home

Base: e5b59caa85e809145cf64eb2a45dc2bc92c3b0e6, the normal merge of the tested classroom candidate and the previous default branch. Product source is unchanged by this task.

Added a root README for Adal: roles, Windows preparation, teacher/student startup, LAN/Wi-Fi scope, recovery, validation evidence and remaining physical acceptance. Kept runtime files under proctoring/ so launch paths remain compatible.

Validation: all 15 relative Markdown links exist; the recovery heading anchor matches; commands and prerequisites were compared with prepare-windows.ps1, Start-Adal.ps1 and the existing operational README. Independent read-only review found no factual or command errors. No device tests were rerun for these documentation-only changes.

Publication plan: fast-forward the existing default branch and create main at the same commit, retaining all prior history. The clone command explicitly selects main. Do not rewrite or delete collaborator branches.

Repository settings blocker: the connected GitHub identity has push=true, admin=false and maintain=false; the available browser is signed out. Changing GitHub's default branch requires the owner's settings access. Creating a main Git ref does not itself change that setting. No product-release or offline-kit readiness claim is made.
