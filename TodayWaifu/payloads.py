"""TodayWaifu 的持久化 JSON 与远程接口载荷类型。

这些结构来自磁盘上的旧版 JSON、数据库 `payload` 列与远程图库接口，属于不可信
外部输入。把它们显式写成 TypedDict，类型检查器与读者都能追踪字段，无需 `Any`；
同时 TypedDict 在运行时不做校验、也不复制数据，因此对性能与旧数据兼容性均无影响。
"""
from __future__ import annotations

from typing import Union, TypedDict

from gsuid_core.models import Message

# `Bot.send` / `target_send` 接受的出站消息形态（与框架签名保持一致）。
# 与框架声明一致可让调用点直接通过类型检查，避免为兼容多种发送形态而退化为 `Any`。
SendMessage = Union[Message, list[Message], str, bytes, list[str]]

# GsCore 配置项 `.data` 的全部可能类型（见 gsuid_core/utils/plugins_config/models.py）。
# 配置值由框架反序列化后不保证形态，读取方必须自行收窄；此处只描述全集，不做强制转换。
ConfigValue = Union[str, bool, int, float, list[str], list[int], dict[str, list[str]], None]


class RoleRecordValue(TypedDict, total=False):
    """单条老婆/老公记录，对应旧 JSON 里的记录值与数据库 `payload` 反序列化结果。

    取值来自旧 JSON 与数据库 `payload` 列的反序列化结果，二者都在类型检查器可见范围
    之外，无法保证键齐全（部分版本仅写入发生变更的字段）。因此声明为 `total=False`：
    缺失字段属于合法状态而非数据损坏，读取方须按缺省语义处理，不可断言键必然存在。
    """

    name: str
    role_ids: list[str]
    image: str
    record_type: str
    target_user_id: str
    display_name: str
    updated_at: int
    created_at: int
    divorced: bool
    divorced_at: int
    stolen_by: str
    stolen_by_name: str
    stolen_from: str
    gifted_to: str
    gifted_to_name: str
    gifted_from: str
    assigned_by: str
    assigned_by_name: str
    safe: bool


# 桶名（wives / husbands / lolis / ...）→ 用户键 → 记录。
# 三层字典的轴向与旧 JSON 及 `payload` 列一致，使序列化结果可直接回写而无需转换；
# 逐层以字符串为键而非列表，是为了按上下文或用户做 O(1) 定位，避免热路径上的线性扫描。
RecordBucket = dict[str, RoleRecordValue]
# 上下文键（群/私聊）→ 桶
DailyContext = dict[str, RecordBucket]
# 日期 → 上下文
DailyDay = dict[str, DailyContext]


class WifeData(TypedDict, total=False):
    """`daily_wife_data.json` / 内存每日数据的顶层结构。

    仅作为旧版单文件存储的兼容载体，新数据以数据库行存放；`total=False` 允许文件
    存在但 `days` 为空（首次启动或导入失败后的残留），调用方需据此走初始化分支。
    """

    days: dict[str, DailyDay]


class GalleryImageEntry(TypedDict, total=False):
    """图库接口里一张图片的描述。

    远程返回可能缺少 `url`（条目被裁剪或字段改名），故不做必填约束；调用方在取值时
    统一做空值过滤，避免单个残缺条目中断整批候选的构建。
    """

    url: str


class GalleryRoleEntry(TypedDict, total=False):
    """图库接口里一个角色的描述。

    `images` 允许字符串与对象混排，因为上游历史版本直接下发 URL 字符串，新版本改为
    下发对象；两种形态并存期间由调用方归一化，此处不做联合或改写。
    """

    role_ids: list[str]
    images: list[GalleryImageEntry | str]


class GalleryPayload(TypedDict, total=False):
    """远程图库接口的响应体。

    接口在空结果或降级响应时可能省略 `roles`；将其视为空集合而非错误，可使熔断与
    回退路径复用同一份解析逻辑。
    """

    roles: list[GalleryRoleEntry]


class RoleAccumulator(TypedDict):
    """角色候选归并中间态（按角色名聚合，无需携带 name）。

    归并键已是角色名，重复保存该字段只会在合并时产生冲突，故不设 `name`。
    """

    role_ids: list[str]
    images: list[str]


class NamedRoleAccumulator(TypedDict):
    """角色候选归并中间态（按归一化名聚合，需保留展示用 name）。

    归并键是去符号化后的名称，与展示名不同，因此必须保留首个候选的 `name` 供界面输出。
    """

    name: str
    role_ids: list[str]
    images: list[str]


class CustomRoleEntry(TypedDict, total=False):
    """自定义老婆条目（对角表 + 图片集合）。

    `images` 存 `(hash_id, path)` 二元组：哈希用于对外展示与删除定位，路径仅供本机
    读取；两者绑定存放可避免删除时再次扫描目录反查。
    """

    role_id: str
    role_name: str
    images: list[tuple[str, str]]


class PendingGift(TypedDict, total=False):
    """待确认的送老婆请求。

    以 `created_at` 的 Unix 秒为过期判据，且读取方必须容忍字段缺失——确认表会在超时
    清理与容量淘汰中被裁剪，残留的半条记录不应导致取值异常。
    """

    created_at: float
    target_user_id: str
    kind: str


class PendingCustomRoleDelete(TypedDict, total=False):
    """待确认的自定义老婆删除请求。

    与送老婆确认共享过期判定语义，但需携带 `role_id` 与 `role_name` 才能在不重新查询
    的情况下完成删除提示与回执。
    """

    created_at: float
    role_id: str
    role_name: str
