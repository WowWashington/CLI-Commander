# Bundled terminal assets

These files are vendored browser dependencies. Their licenses apply to the respective upstream code; they do not define a license for CLI Commander.

| Package | Version | Bundled files | License notice |
| --- | --- | --- | --- |
| `@xterm/xterm` | 5.5.0 | `xterm.min.js`, `xterm.min.css` | [MIT license and copyright](LICENSE.xterm.txt) |
| `@xterm/addon-fit` | 0.10.0 | `addon-fit.min.js` | [MIT license and copyright](LICENSE.addon-fit.txt) |

Sources:

- [xterm.js 5.5.0 source and license](https://github.com/xtermjs/xterm.js/tree/5.5.0).
- [Fit addon package at that release](https://github.com/xtermjs/xterm.js/blob/5.5.0/addons/addon-fit/package.json).
- Published package license files: [xterm](https://cdn.jsdelivr.net/npm/@xterm/xterm@5.5.0/LICENSE) and [addon-fit](https://cdn.jsdelivr.net/npm/@xterm/addon-fit@0.10.0/LICENSE).

The minified assets identify their exact original package versions in their headers. Keep these copyright and license notices with any redistributed copies. If updating the assets, verify the upstream version and replace the corresponding notices as needed.

Python dependencies are installed separately from `requirements.lock`; their distribution metadata includes their respective licenses.
