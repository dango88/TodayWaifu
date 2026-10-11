# 跨平台 @ 目标解析与私聊消息适配的契约。
#
# 目标用户 ID 的上报形态随协议端而变：OneBot 用数字 QQ 号，QQ 官方机器人等平台用 openid
# （16 位以上十六进制），部分适配器还把整段 CQ 码或 <at> 富文本写入结构化字段。解析次序
# 与自身 ID 排除必须稳定，否则「抢/送老婆」会作用于错误对象；解析失败时必须返回 None 让
# 用户显式 @ 目标，回退到发送者本人会让「送老婆」静默把老婆送给自己。
#
# 该文件的第二组断言锁定私聊适配边界与图片发送路径：私聊只允许剥离 @ 段，
# 不得删除个人号依赖的富文本目标信息，也不得重新引入已在 6b74175 移除的官方机器人
# Markdown 图库集成。
import ast
import unittest
from types import SimpleNamespace
from typing import Any
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class FakeMessage:
    def __init__(self, type: str, data: Any):
        self.type = type
        self.data = data


def _twf_module_defining(name: str) -> Path:
    """返回定义 name 的 TodayWaifu 模块路径，避免测试与模块划分方式耦合。

    shared.py 已按职责拆分（bc6ab41），目标函数所在文件会随职责调整迁移，故按定义位置
    动态定位，而非写死导入路径。
    """
    for path in sorted((ROOT / 'TodayWaifu').glob('*.py')):
        tree = ast.parse(path.read_text(encoding='utf-8-sig'))
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
                return path
    raise AssertionError(f'{name} 未在任何 TodayWaifu 模块中定义')


def _twf_source_defining(name: str) -> str:
    return _twf_module_defining(name).read_text(encoding='utf-8-sig')


def _load_functions(names: set[str], config: dict[str, Any] | None = None) -> dict[str, Any]:
    # 按函数名跨文件收集并注入替身全局名：解析逻辑引用 re、Event、Message 等框架符号，
    # 这些名字不随函数体抽取，缺失会直接抛 NameError。
    # 同时以单例 FakeMessage 代替框架的 Message，使断言可以按消息段对象比较。
    body: list[ast.stmt] = [
        ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0),
        ast.ImportFrom(module='typing', names=[ast.alias(name='Any')], level=0),
    ]
    for path in sorted((ROOT / 'TodayWaifu').glob('*.py')):
        tree = ast.parse(path.read_text(encoding='utf-8-sig'))
        body.extend(
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names
        )
    values = config or {}
    namespace: dict[str, Any] = {
        'Any': Any,
        'Bot': object,
        'Event': object,
        'Message': FakeMessage,
        're': __import__('re'),
        '_cfg': lambda key: values.get(key, ''),
        '_cfg_bool': lambda key, default=False: bool(values.get(key, default)),
    }
    module = ast.Module(body=body, type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, 'mentions', 'exec'), namespace)
    return namespace


class PlatformMentionTests(unittest.TestCase):
    def test_personal_target_formats_are_supported(self) -> None:
        # 逐形态锁定解析优先级：结构化 qq 字段、CQ 码、以及纯文本中 @ 与命令词后缀。
        # 每种形态都对应一个真实协议端的实际上报格式，漏掉一种即该平台无法使用抢/送老婆。
        functions = _load_functions(
            {
                '_normalise_target_user_id',
                '_target_user_id_from_text',
                '_self_user_ids',
                '_iter_event_messages',
                '_get_event_target_user_id',
            }
        )
        normalise = functions['_normalise_target_user_id']
        parse_text = functions['_target_user_id_from_text']
        get_target = functions['_get_event_target_user_id']

        self.assertEqual(normalise({'qq': '123456789'}), '123456789')
        self.assertEqual(normalise({'user_id': '10001'}), '10001')
        self.assertEqual(parse_text('[CQ:at,qq=123456789]'), '123456789')
        self.assertEqual(parse_text('抢老婆 @123456789'), '123456789')
        self.assertEqual(parse_text('送老婆 123456789'), '123456789')
        self.assertEqual(
            get_target(
                SimpleNamespace(
                    at_list=None,
                    at=None,
                    target_id=None,
                    target_user_id=None,
                    content=[FakeMessage('at', {'qq': '123456789'})],
                    message=None,
                    original_message=None,
                    raw_text='',
                )
            ),
            '123456789',
        )

    def test_target_user_id_falls_back_to_personal_message_text(self) -> None:
        # 结构化字段全空时须回落到个人号纯文本（'QQ:123456789' 形态）：这是 OpenID 平台
        # 之外最常见的上报方式，缺失该回落会让只发文本 @ 的适配器完全无法指定目标。
        functions = _load_functions(
            {
                '_normalise_target_user_id',
                '_target_user_id_from_text',
                '_self_user_ids',
                '_iter_event_messages',
                '_get_event_target_user_id',
            }
        )
        get_target = functions['_get_event_target_user_id']
        event = SimpleNamespace(
            at_list=None,
            at=None,
            target_id=None,
            target_user_id=None,
            content=None,
            message=None,
            original_message=None,
            text='抢老婆 QQ:123456789',
            raw_text='',
            raw_message='',
        )
        self.assertEqual(get_target(event), '123456789')

    def test_generic_send_boundary_only_removes_private_mentions(self) -> None:
        # 适配只在私聊生效，群聊必须原样返回同一对象：群聊 @ 段属用户可见内容，
        # 一旦群聊也剥离，群友间的互动回复会丢失目标指向。
        # 私聊剥离后还需连带删除紧随其后的换行，否则消息首行留空。
        functions = _load_functions(
            {'_is_at_message', '_remove_private_mentions', '_adapt_mentions_for_platform'}
        )
        adapt = functions['_adapt_mentions_for_platform']
        outgoing = [FakeMessage('at', '123456789'), '\n', '结果文字', FakeMessage('image', 'x')]

        group_bot = SimpleNamespace(ev=SimpleNamespace(user_type='group'))
        self.assertIs(adapt(group_bot, outgoing), outgoing)

        direct_bot = SimpleNamespace(ev=SimpleNamespace(user_type='direct'))
        direct = adapt(direct_bot, outgoing)
        self.assertEqual(direct[0], '结果文字')
        self.assertEqual(direct[1].type, 'image')

    def test_result_image_senders_keep_personal_message_segments(self) -> None:
        # 图片段必须经 _image_message 构造：它在插件线程池内完成 base64，
        # 避免框架的 MessageSegment.image(bytes) 在事件循环上同步编码大图（de7fd79）。
        # 以源码文本检查而非行为，是因为该约束正是「不得在循环内编码」这一实现要求本身。
        source = (ROOT / 'TodayWaifu' / 'senders.py').read_text(encoding='utf-8')
        for function_name in ('_deliver_role_image', '_deliver_loli_result_image', '_send_local_image'):
            start = source.index(f'async def {function_name}(')
            next_function = source.find('\nasync def ', start + 1)
            block = source[start:next_function if next_function >= 0 else None]
            # 图片段必须经 _image_message 构造：它在插件线程池里做 base64，
            # 避免框架的 MessageSegment.image(bytes) 在事件循环上同步编码大图
            self.assertIn('await _image_message(', block)
            self.assertNotIn('MessageSegment.image(image', block)

    def test_personal_compatibility_is_not_removed(self) -> None:
        # 个人号兼容能力不得因移除官方机器人分支而一并删除：CQ 码解析与 QQ 头像地址
        # 是个人号链路的基础，而官方机器人 Markdown 图库已在 6b74175 整体移除，
        # 其函数名与配置键不得以任何形式回流。
        combined = '\n'.join(
            path.read_text(encoding='utf-8-sig') for path in sorted((ROOT / 'TodayWaifu').glob('*.py'))
        )
        self.assertIn('_target_user_id_from_text', combined)
        self.assertIn('_qq_avatar_url', combined)
        self.assertIn('CQ:at', combined)
        self.assertNotIn('_try_send_official_qq_image_markdown', combined)
        self.assertNotIn('DailyWifeOfficialImageGalleryUrl', combined)
        self.assertNotIn('DailyWifeOfficialImageGalleryToken', combined)

    def test_private_account_prompts_remain_compatible(self) -> None:
        # 无数字 QQ 号的平台需要提示用户改用 @ 或复制 ID 的文案：这三处文案是用户侧
        # 唯一的操作指引，随平台适配重构被删会让该平台用户无从完成抢/送老婆。
        daily = (ROOT / 'TodayWaifu' / 'daily.py').read_text(encoding='utf-8')
        rob = (ROOT / 'TodayWaifu' / 'rob.py').read_text(encoding='utf-8')
        gift = (ROOT / 'TodayWaifu' / 'gift.py').read_text(encoding='utf-8')
        self.assertIn('CQ:at', daily)
        self.assertIn('对方 QQ', rob)
        self.assertIn('对方 QQ', gift)

    def test_daily_loli_results_use_the_shared_sender(self) -> None:
        # 萝莉结果必须复用共享发送器，而非自建发送路径：私有路径会绕过图片体积压缩与
        # 线程池编码，在图片偏大的平台上直接发送失败。
        source = (ROOT / 'TodayWaifu' / 'loli.py').read_text(encoding='utf-8')
        self.assertIn('_send_loli_result_image(', source)

    def test_assignment_has_no_platform_specific_branch(self) -> None:
        # 分配老婆/分配老公走统一的 _send_role_image，不得出现平台专用分支：分支一旦存在，
        # 新增平台时该功能会被静默绕过，表现为特定协议端收不到分配结果图。
        source = (ROOT / 'TodayWaifu' / 'daily.py').read_text(encoding='utf-8')
        assignment_start = source.index('async def _send_assign_daily(')
        assignment_end = source.index('\nasync def ', assignment_start + 1)
        assignment = source[assignment_start:assignment_end]
        self.assertNotIn('official_qq_mention', assignment)
        self.assertIn('_send_role_image(', assignment)


if __name__ == '__main__':
    unittest.main()
