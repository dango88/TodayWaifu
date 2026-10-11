# 分配指令契约：主人分配老婆 / 分配老公共用同一条实现（_send_assign_daily）。
#
# 分配是主人的显式归属意志，两条链路都必须：
# 1. 写入 assigned_by / assigned_by_name 标记（分配保护的判定依据，见 test_assigned_wife_protection）；
# 2. 老公功能未启用时静默忽略（与抢/送老公保持同一入口判定，不产生任何副作用）；
# 3. 分配老婆连带清除补偿记录（safe_wives 与 wives 同人同日互斥）；老公模式无补偿桶，不删除。
#
# 与 test_safe_wife_divorce 相同，被测函数带相对导入，测试按 AST 抽取单个函数后
# 只注入其真正引用的名称，在不加载插件运行时的前提下锁定行为。
import re
import ast
import asyncio
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import Mock, AsyncMock

ROOT = Path(__file__).resolve().parents[1]


def _extract_function(filename, name, namespace):
    tree = ast.parse((ROOT / 'TodayWaifu' / filename).read_text(encoding='utf-8-sig'))
    function = next(
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
    )
    module = ast.Module(
        body=[
            ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0),
            function,
        ],
        type_ignores=[],
    )
    ast.fix_missing_locations(module)
    exec(compile(module, filename, 'exec'), namespace)
    return namespace[name]


ROLE = SimpleNamespace(name='长离', role_ids=('1205',), image='a.png')


def _build_namespace(saved, messages, *, husband_available=True, is_master=True):
    namespace = {
        'LOG_PREFIX': '',
        'logger': Mock(),
        'random': SimpleNamespace(random=lambda: 0.5),
        '_can_assign_wife': lambda ev: is_master,
        '_get_event_target_user_id': lambda ev: 'u2',
        '_user_key': lambda ev, user_id=None: 'u1',
        'name_from_event': lambda ev, user_id=None: '主人',
        '_safe_send': messages,
        '_load_candidates': AsyncMock(return_value=([ROLE], None)),
        '_filter_by_mode': lambda candidates, mode: candidates,
        '_pick_role_record': lambda candidates, rng: candidates[0],
        '_record_to_dict': lambda record, ev, user_id: {'name': record.name, 'image': record.image},
        '_daily_item_title': {'wife': '老婆', 'husband': '老公'}.__getitem__,
        '_daily_bucket_name': {'wife': 'wives', 'husband': 'husbands'}.__getitem__,
        '_husband_available': lambda: husband_available,
        '_daily_context_lock': lambda ev: asyncio.Lock(),
        '_save_daily_records': saved,
        '_send_role_image': AsyncMock(),
    }
    namespace['_assignment_role_name'] = _extract_function(
        'daily.py', '_assignment_role_name', {'re': re}
    )
    namespace['_find_assignable_wife'] = _extract_function(
        'daily.py', '_find_assignable_wife', {'_normalize_role_name': lambda name: name}
    )
    return namespace


class AssignmentRoleNameTests(unittest.TestCase):
    def setUp(self):
        self.parse = _extract_function('daily.py', '_assignment_role_name', {'re': re})
        self.ev = SimpleNamespace(text='')

    def test_plain_role_name_passes_through(self):
        self.ev.text = '长离'
        self.assertEqual(self.parse(self.ev, 'u2'), '长离')

    def test_command_words_are_stripped_for_both_kinds(self):
        # 分配词与类型词都必须剔除，否则按名查找会带着「分配老公」前缀查不到角色
        self.ev.text = '分配老公长离'
        self.assertEqual(self.parse(self.ev, 'u2'), '长离')
        self.ev.text = '分配老婆角鳄'
        self.assertEqual(self.parse(self.ev, 'u2'), '角鳄')

    def test_at_mention_and_target_id_are_removed(self):
        self.ev.text = '[CQ:at,qq=u2] 给 u2 长离'
        self.assertEqual(self.parse(self.ev, 'u2'), '长离')

    def test_empty_text_returns_empty(self):
        self.assertEqual(self.parse(self.ev, 'u2'), '')


class AssignDailyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.ev = SimpleNamespace(user_id='u1', group_id='g1', text='长离')
        self.bot = object()
        self.messages = AsyncMock()
        self.saved = AsyncMock()
        self.role_image = AsyncMock()

    def _run(self, kind, **kwargs):
        namespace = _build_namespace(self.saved, self.messages, **kwargs)
        namespace['_send_role_image'] = self.role_image
        assign = _extract_function('daily.py', '_send_assign_daily', namespace)
        return assign(self.bot, self.ev, kind)

    async def test_assign_wife_saves_to_wives_bucket_and_clears_safe_wives(self):
        await self._run('wife')
        updates = self.saved.await_args.args[1]
        deletes = self.saved.await_args.args[2]
        self.assertEqual((updates[0][0], updates[0][1]), ('wives', 'u2'))
        self.assertEqual(updates[0][2]['assigned_by'], 'u1')
        self.assertEqual(deletes, [('safe_wives', 'u2')])
        self.role_image.assert_awaited_once()
        self.assertIn('老婆', self.role_image.await_args.args[3])

    async def test_assign_husband_saves_to_husbands_bucket_without_deletes(self):
        await self._run('husband')
        updates = self.saved.await_args.args[1]
        deletes = self.saved.await_args.args[2]
        self.assertEqual((updates[0][0], updates[0][1]), ('husbands', 'u2'))
        self.assertEqual(updates[0][2]['assigned_by'], 'u1')
        self.assertEqual(deletes, [])
        self.role_image.assert_awaited_once()
        self.assertIn('老公', self.role_image.await_args.args[3])
        self.assertEqual(self.role_image.await_args.args[6], 'husband')

    async def test_assign_husband_is_silent_when_husband_disabled(self):
        # 与抢/送老公一致：功能关闭时入口直接忽略，不提示也不落库
        await self._run('husband', husband_available=False)
        self.saved.assert_not_awaited()
        self.role_image.assert_not_awaited()
        self.messages.assert_not_awaited()

    async def test_assign_wife_still_works_when_husband_disabled(self):
        # 老公开关只影响老公模式：分配老婆不受牵连
        await self._run('wife', husband_available=False)
        self.saved.assert_awaited_once()

    async def test_assign_refuses_non_whitelisted_user(self):
        await self._run('wife', is_master=False)
        self.saved.assert_not_awaited()
        self.messages.assert_awaited_once_with(self.bot, '只有机器人主人或分配老婆白名单用户可以分配老婆。')

    async def test_assign_husband_refusal_message_names_husband(self):
        await self._run('husband', is_master=False)
        self.saved.assert_not_awaited()
        self.messages.assert_awaited_once_with(self.bot, '只有机器人主人或分配老公白名单用户可以分配老公。')

    async def test_assign_unknown_role_reports_kind_title(self):
        self.ev.text = '不存在的角色'
        await self._run('husband')
        self.saved.assert_not_awaited()
        self.messages.assert_awaited_once_with(self.bot, '未找到名为“不存在的角色”的老公角色。')


class AssignHusbandProtectionTests(unittest.IsolatedAsyncioTestCase):
    """分配老公沿用分配保护：assigned_by 标记使老公记录同样不可离婚、被抢或被送。"""

    def setUp(self):
        self.ev = SimpleNamespace(user_id='u1', group_id='g1')
        self.bot = object()
        self.context = {
            'husbands': {
                'u1': {
                    'name': '漂泊者',
                    'assigned_by': 'master1',
                    'assigned_by_name': '主人',
                }
            },
            'wives': {},
            'safe_wives': {},
            'marry_members': {},
        }
        self.messages = AsyncMock()
        self.saved = AsyncMock()
        self.namespace = {
            'LOG_PREFIX': '',
            'logger': Mock(),
            'time': SimpleNamespace(time=lambda: 123),
            '_user_key': lambda ev: ev.user_id,
            '_safe_send': self.messages,
            '_daily_bucket_name': {'wife': 'wives', 'husband': 'husbands'}.__getitem__,
            '_daily_context_lock': lambda ev: asyncio.Lock(),
            '_load_daily_context': AsyncMock(return_value=self.context),
            '_save_daily_records': self.saved,
            '_divorce_result_name': lambda kind, name: name,
        }
        self.namespace['_wife_state'] = _extract_function('daily_store.py', '_wife_state', {})
        self.namespace['_is_assigned_wife'] = _extract_function('daily_store.py', '_is_assigned_wife', {})
        self.divorce = _extract_function('divorce.py', '_send_divorce', self.namespace)

    async def test_assigned_husband_cannot_be_divorced(self):
        await self.divorce(self.bot, self.ev, 'husband')
        self.saved.assert_not_awaited()
        self.assertFalse(self.context['husbands']['u1'].get('divorced'))
        self.messages.assert_awaited_once_with(self.bot, '主人分配的老公不能离婚哦~')


if __name__ == '__main__':
    unittest.main()
