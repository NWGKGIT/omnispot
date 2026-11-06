# Changelog

## [1.0.3] - 2026-11-06

### Fixed

- Fixed the hotspot icon appearing below the bar after an Omarchy update.
  Use the bar's built-in glyph renderer to center the icon by its painted
  bounds, avoiding a binding loop in the custom icon renderer.
- Qualified the bar icon's theme color references to avoid a QML name conflict
  and preserve the accent color when the hotspot is active.

### Updating

Run `omarchy plugin update omnispot.omarchy`, then `omarchy restart shell`
to replace the shell's cached QML components.

[1.0.3]: https://github.com/NWGKGIT/omnispot/compare/v1.0.2...v1.0.3
