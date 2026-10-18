# BMC for Home Assistant

通过 Home Assistant 蓝牙适配器或**可连接的 ESPHome Bluetooth Proxy**，在本地接入 **BMC E5 B25A Plus** 呼吸机。支持蓝牙发现、中文配置界面、状态读取和受限的手动控制。

这是非官方集成，与 BMC / 瑞迈特无隶属关系；不提供诊断、治疗建议或医疗报警保障。不要把 Home Assistant 或本集成作为维持治疗的必要条件。设备本身和医生给出的治疗设置始终优先。

## HACS 安装

1. 打开 **HACS → 右上角菜单 → 自定义存储库**。
2. 添加 `https://github.com/dajialin/home-assistant-bmc`，类型选择 **Integration / 集成**。
3. 下载 **BMC**，选择最新正式 Release。
4. 将自己的本地协议文件放在 `/config/bmc_e5_protocol.json`，详见下节。**HACS 下载代码不会生成这个文件。**
5. 重启 Home Assistant；在“设置 → 设备与服务”接受发现，或添加 **BMC** 并输入设备蓝牙地址。首次连接会验证认证、型号和实时数据。

本仓库可作为 **HACS 自定义存储库**安装；不代表已进入 HACS 默认商店目录。

### 本地协议文件（必需）

认证材料只由使用者在本地提供，不包含在仓库、Release 或安装包中。需要自行合法取得并安装 **LightTrip 1.02.04 的 arm64 APK**，在自己的计算机上运行：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-tools.txt
.venv/bin/python -m tools.extract_profile --apk /path/to/LightTrip.apk --output bmc_e5_protocol.json
```

Windows 可将 `.venv/bin/python` 换成 `.venv\Scripts\python.exe`。工具只接受已核对的原生库 SHA-256；不匹配的版本会拒绝处理，不要跳过校验。

将生成文件安全复制到 HA 的 `/config/`，限制为仅管理员可读，例如 `chmod 600 bmc_e5_protocol.json`。**不要提交到 Git，不要贴到 issue 或聊天中，也不要上传 APK、原生库或蓝牙抓包。**

如果此前已经部署过本集成，保留原来的 `/config/bmc_e5_protocol.json` 即可；HACS 更新只替换 `custom_components/bmc_e5/`，不会替换配置目录根部的协议文件。

### 从旧版手动安装迁移

先备份 `custom_components/bmc_e5/`、协议文件和 HA 配置，再通过上述 HACS 自定义仓库下载新版本并重启。**不要删除现有 BMC 集成条目，也不需要重新配对。**内部域名仍为 `bmc_e5`，已有实体 ID 保持不变。

### 手动安装

将 Release 的 `bmc_e5-VERSION.zip` 解压到 `/config/custom_components/`，最终路径应为 `/config/custom_components/bmc_e5/manifest.json`。协议文件和重启要求与 HACS 相同。

## 支持范围

当前仅针对 **E5 B25A Plus / AutoS**。其他型号、模式、固件和手机同时连接未验证。请先断开手机 LightTrip 连接，并确保蓝牙代理支持主动连接。

- **12 个只读实体**：蓝牙连接、治疗中、运行状态代码、治疗模式、压力单位、最小呼气压力设定、最大吸气压力设定、延时升压、呼吸减压档位、自动启动、自动停机、延时关机。
- **16 个控制实体**：治疗启停、预热、延时升压、呼吸减压、湿化器、加热管、自动启动/停机、延时关机、管路类型、面罩类型、水量提示，以及四种耗材提醒周期。
- 压力实体表示**当前设定值**，不是瞬时测量值。没有诊疗报告、历史统计、压力/模式写入、耗材使用时长清零或佩戴测试。

### 控制与断线保护

- 仅明确调用控制实体才会创建控制事务；启动、添加、重连或重载不会自动开始/停止治疗。
- 设置变更只允许在已确认的待机状态进行。每次读取当前完整设置，只改指定字段，等待设备应答并完整回读；没有乐观成功状态。
- 一次只执行一个事务。失败、超时、拒绝或断线后**不自动重发控制命令**，不会排队到下次重连执行。
- 断线或实时数据过期会将数据实体标为不可用；**断线不等于治疗停止**。蓝牙重连与控制重试是不同的行为。
- 本次兼容修复不改变既有控制协议；自动化测试使用构造数据和模拟传输，不代表所有控制都经过实机动作验收。

## 蓝牙依赖兼容性

旧版本固定 `bleak-retry-connector==4.6.3`，会与 Home Assistant 2026.9.4 要求的 `4.7.0` 冲突。新版本通过声明依赖 HA 内置 `bluetooth` 集成，使用 **Core 自己管理的 Bluetooth / connector 版本**，不再试图安装第二个冲突版本。不要手动降级 Core 的蓝牙库。

最低支持版本保守设为 **Home Assistant 2026.9.4**。未来 Core 更新仍可能带来 API 变动；版本约束修复并非永久兼容保证。

## 开发与验证

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-tools.txt
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m tools.build_package
```

协议、控制事务和安全输入测试完全离线；普通开发环境没有 `homeassistant` 时，真实 HA 类测试会明确跳过。这部分测试应另在与目标 Core 匹配的环境运行，仍使用模拟传输，不调用真实设备。

不要将私有采样、设备地址、家庭配置、认证材料或完整 HA 诊断文件提交到仓库。报告问题时只附已脱敏的版本、错误类型和必要日志。

## License

MIT，见 [LICENSE](LICENSE)。品牌图片仅用于标识对应设备；相关商标权归其权利人，本项目不主张商标所有权。
