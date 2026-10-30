# 第三方素材与依赖

- [Primer React](https://github.com/primer/react)、[Primer Primitives](https://github.com/primer/primitives)：MIT；通过锁文件固定依赖，颜色使用 functional tokens。
- [Octicons](https://github.com/primer/octicons)：MIT；`src/assets/` 是已确认 Figma 设计导出的对应矢量，保留图层的颜色和固有尺寸；许可保留于 `assets/OCTICONS-LICENSE`。没有运行时临时 Figma URL。
- [Mona Sans](https://github.com/github/mona-sans)、[Noto Sans SC](https://github.com/notofonts/noto-cjk)、[Cascadia Code / Mono](https://github.com/microsoft/cascadia-code)：SIL Open Font License 1.1；通过 Fontsource 包本地加载，原许可随依赖保留。
- [React](https://github.com/facebook/react)、[Electron](https://github.com/electron/electron)、[Vite](https://github.com/vitejs/vite)：MIT；[Playwright](https://github.com/microsoft/playwright)：Apache-2.0。依赖原许可保留于各包目录。

`assets/tray.png` 为本项目生成的 R 字母托盘占位标记，不使用 Jenkins 品牌或吉祥物。安装图标沿用现有 Octicons 仓库标记，增加项目蓝色背景；转换脚本为 `scripts/build-icon.cjs`，原 MIT 许可继续保留。

Windows 安装程序内置 CPython 和 Python 依赖，使用 PyInstaller 的分发例外构建服务；CPython／PyInstaller 及各依赖的原许可文本收集到 `resources/third-party-licenses/`。Electron 的许可和 Chromium 通告随发行物保留。该目录另有构建环境依赖索引，不表示索引中的所有开发工具都在运行时加载。
