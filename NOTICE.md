# Third-Party Credits

codevet ships with automatic integration of the following third-party tool.

## llmfit

**codevet's hardware-fit preflight check is powered by llmfit.**

- **Repository:** https://github.com/AlexsJones/llmfit
- **Author:** Alex Jones ([@AlexsJones](https://github.com/AlexsJones))
- **License:** MIT

llmfit is a Rust CLI that detects host hardware and scores LLM models
against it. codevet downloads the official prebuilt llmfit binary from
the upstream GitHub release on first use and caches it locally. We do
not redistribute the binary in this repository — every install pulls
it fresh from the author's releases and stays up-to-date with the
latest version automatically (24-hour cache on the version lookup).

Huge thanks to Alex for building llmfit and making it MIT-licensed.
Without it, codevet would have no good way to tell users in advance
whether their chosen model will actually run on their machine.

If you find llmfit useful, please star the upstream repo:
https://github.com/AlexsJones/llmfit

---

All other dependencies are listed in `pyproject.toml` with their
respective licenses preserved via PyPI metadata.
