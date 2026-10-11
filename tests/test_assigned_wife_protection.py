# 主人分配老婆的保护契约：分配记录（携带 assigned_by 标记）不可被离婚、被抢或转送。
#
# 「分配老婆」由主人整条重写目标用户的当日记录并写入 assigned_by / assigned_by_name，
# 该归属是主人的显式意志，因此三条流转链路都必须在持锁校验中拦截分配记录：
# 离婚（用户单方面解除）、抢（他人夺取）、送（转赠他人）。拦截放在 `_wife_state` 的
# owned 判定之后、二手判定之前：分配记录必然是 owned，且其拒绝文案与二手不同，
# 不能并入 `_is_secondhand_wife`。
#
# 与 test_safe_wife_divorce 相同，被测函数带相对导入，测试按 AST 抽取单个函数后
# 只注入其真正引用的名称，在不加载插件运行时的前提下锁定行为。
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


def _extract_record_helpers():
    """从 daily_store.py 抽取来源/分配判定助手，保持与运行时同一份实现。"""
    namespace = {}
    _extract_function('daily_store.py', '_wife_state', namespace)
    _extract_function('daily_store.py', '_wife_origin', namespace)
    _extract_function('daily_store.py', '_is_assigned_wife', namespace)
    _extract_function('daily_store.py', '_is_secondhand_wife', namespace)
    _extract_function('daily_store.py', '_has_active_wife', namespace)
    return namespace


ASSIGNED_RECORD = {
    'name': '分配老婆',
    'role_ids': ['1001'],
    'image': 'a.png',
    'record_type': 'role',
    'assigned_by': 'master1',
    'assigned_by_name': '主人',
}


class AssignedWifeHelperTests(unittest.TestCase):
    def setUp(self):
        self.helpers = _extract_record_helpers()

    def test_assigned_record_classified_as_assigned(self):
        self.assertEqual(self.helpers['_wife_origin'](ASSIGNED_RECORD), 'assigned')
        self.assertTrue(self.helpers['_is_assigned_wife'](ASSIGNED_RECORD))

    def test_assigned_marker_takes_priority_over_stale_flow_markers(self):
        # 分配记录由整条重写产生，正常不会携带流转标记；若历史脏数据同时带有
        # stolen_from，分配标记仍是当前有效来源，判定不得被旧标记翻转。
        record = {**ASSIGNED_RECORD, 'stolen_from': 'u9'}
        self.assertEqual(self.helpers['_wife_origin'](record), 'assigned')

    def test_plain_and_non_dict_records_are_not_assigned(self):
        self.assertFalse(self.helpers['_is_assigned_wife']({'name': '路人'}))
        self.assertFalse(self.helpers['_is_assigned_wife'](None))
        self.assertFalse(self.helpers['_is_assigned_wife']('assigned_by'))

    def test_existing_origin_classification_unchanged(self):
        # 分配来源不得影响既有口径：二手判定仍只认抢来 / 送来 / 补偿三类。
        classify = self.helpers['_wife_origin']
        self.assertEqual(classify({'name': '路人'}), 'self')
        self.assertEqual(classify({'name': '路人', 'stolen_from': 'u2'}), 'robbed')
        self.assertEqual(classify({'name': '路人', 'gifted_from': 'u2'}), 'gifted')
        self.assertEqual(classify({'name': '路人', 'safe': True}), 'safe')
        self.assertEqual(classify(None), 'self')
        self.assertEqual(classify(ASSIGNED_RECORD), 'assigned')
        secondhand = self.helpers['_is_secondhand_wife']
        self.assertTrue(secondhand({'stolen_from': 'u2'}))
        self.assertTrue(secondhand({'gifted_from': 'u2'}))
        self.assertTrue(secondhand({'safe': True}))
        self.assertFalse(secondhand(ASSIGNED_RECORD))
        self.assertFalse(secondhand({'name': '路人'}))


class AssignedWifeDivorceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.ev = SimpleNamespace(user_id='u1', group_id='g1')
        self.bot = object()
        self.context = {
            'wives': {'u1': dict(ASSIGNED_RECORD)},
            'safe_wives': {},
            'husbands': {},
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
            '_daily_context_lock': lambda ev: self.lock,
            '_load_daily_context': AsyncMock(return_value=self.context),
            '_save_daily_records': self.saved,
            '_divorce_result_name': lambda kind, name: name,
        }
        self.namespace['_wife_state'] = _extract_record_helpers()['_wife_state']
        self.namespace['_is_assigned_wife'] = _extract_record_helpers()['_is_assigned_wife']
        self.lock = asyncio.Lock()
        self.divorce = _extract_function('divorce.py', '_send_divorce', self.namespace)

    async def test_assigned_wife_cannot_be_divorced(self):
        await self.divorce(self.bot, self.ev, 'wife')
        self.saved.assert_not_awaited()
        self.assertFalse(self.context['wives']['u1'].get('divorced'))
        self.messages.assert_awaited_once_with(self.bot, '主人分配的老婆不能离婚哦~')

    async def test_unassigned_wife_still_divorces(self):
        # 回归守卫：无 assigned_by 标记的记录不受拦截，离婚行为保持原样。
        del self.context['wives']['u1']['assigned_by']
        await self.divorce(self.bot, self.ev, 'wife')
        self.saved.assert_awaited_once()
        updates = self.saved.await_args.args[1]
        self.assertEqual((updates[0][0], updates[0][1]), ('wives', 'u1'))
        self.assertTrue(updates[0][2]['divorced'])
        self.messages.assert_awaited_once_with(self.bot, '已经和今天的老婆离婚：分配老婆。')


class AssignedWifeRobTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.ev = SimpleNamespace(user_id='u1', group_id='g1')
        self.bot = object()
        self.context = {
            'wives': {'u2': dict(ASSIGNED_RECORD)},
            'rob_attempts': {},
        }
        self.messages = AsyncMock()
        self.saved = AsyncMock()
        self.helpers = _extract_record_helpers()
        self.target_record = SimpleNamespace(image='a.png', to_role=lambda: None)
        self.namespace = {
            'LOG_PREFIX': '',
            'logger': Mock(),
            'random': SimpleNamespace(random=lambda: 0.0),
            '_daily_item_title': lambda kind: '老婆',
            '_husband_available': lambda: True,
            '_rob_enabled': lambda kind: True,
            '_rob_success_rate': lambda kind: 0.5,
            '_get_event_target_user_id': lambda ev: 'u2',
            '_user_key': lambda ev, user_id=None: user_id or ev.user_id,
            '_safe_send': self.messages,
            '_get_existing_daily_record': AsyncMock(return_value=self.target_record),
            '_daily_context_lock': lambda ev: self.lock,
            '_load_daily_context': AsyncMock(return_value=self.context),
            '_daily_bucket_name': {'wife': 'wives'}.__getitem__,
            '_is_master': lambda ev: False,
            '_record_to_dict': lambda record, ev, user_id: {'name': '分配老婆'},
            'name_from_event': lambda ev, user_id: '抢夺者',
            '_save_daily_records': self.saved,
            '_send_rob_result_image': AsyncMock(),
            '_build_rob_success_text': lambda role, target, kind: 'ok',
        }
        self.namespace.update(self.helpers)
        self.lock = asyncio.Lock()
        self.rob = _extract_function('rob.py', '_send_rob_daily', self.namespace)

    async def test_assigned_wife_cannot_be_robbed(self):
        await self.rob(self.bot, self.ev, 'wife')
        self.saved.assert_not_awaited()
        self.assertEqual(self.context['rob_attempts'], {})
        self.assertIn('assigned_by', self.context['wives']['u2'])
        self.assertNotIn('stolen_by', self.context['wives']['u2'])
        self.messages.assert_awaited_once_with(self.bot, '对方的老婆是主人分配的，抢不动哦~')

    async def test_rob_refusal_does_not_consume_daily_attempt(self):
        # 拒绝属于前置校验短路：与既有拒绝分支一致，不消耗当日抢夺次数。
        await self.rob(self.bot, self.ev, 'wife')
        self.assertNotIn('u1', self.context['rob_attempts'])


class AssignedWifeGiftTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.ev = SimpleNamespace(user_id='u1', group_id='g1')
        self.bot = object()
        self.context = {
            'wives': {'u1': dict(ASSIGNED_RECORD)},
        }
        self.messages = AsyncMock()
        self.saved = AsyncMock()
        self.helpers = _extract_record_helpers()
        self.giver_record = SimpleNamespace(image='a.png', to_role=lambda: None)
        self.pending = Mock()
        self.namespace = {
            'LOG_PREFIX': '',
            'logger': Mock(),
            'MessageSegment': Mock(),
            'GIFT_CONFIRM_TIMEOUT_SECONDS': 60,
            '_daily_item_title': lambda kind: '老婆',
            '_husband_available': lambda: True,
            '_gift_enabled': lambda kind: True,
            '_get_event_target_user_id': lambda ev: 'u2',
            '_user_key': lambda ev, user_id=None: user_id or ev.user_id,
            '_safe_send': self.messages,
            '_get_existing_daily_record': AsyncMock(return_value=self.giver_record),
            '_daily_context_lock': lambda ev: self.lock,
            '_load_daily_context': AsyncMock(return_value=self.context),
            '_daily_bucket_name': {'wife': 'wives'}.__getitem__,
            '_get_pending_gift': self.pending.get_pending,
            '_set_pending_gift': self.pending.set_pending,
            '_clear_pending_gift': self.pending.clear_pending,
            '_record_from_dict': Mock(return_value=None),
            '_record_to_dict': lambda record, ev, user_id: {'name': '分配老婆'},
            'name_from_event': lambda ev, user_id: '对方',
            '_save_daily_records': self.saved,
        }
        self.namespace.update(self.helpers)
        self.lock = asyncio.Lock()
        self.gift_send = _extract_function('gift.py', '_send_gift_daily', self.namespace)
        self.gift_accept = _extract_function('gift.py', '_accept_gift_daily', self.namespace)

    async def test_assigned_wife_cannot_be_gifted(self):
        await self.gift_send(self.bot, self.ev, 'wife')
        self.pending.set_pending.assert_not_called()
        self.saved.assert_not_awaited()
        self.messages.assert_awaited_once_with(self.bot, '这个老婆是主人分配的，不能送出去哦~')

    async def test_pending_gift_to_assigned_wife_fails_on_accept(self):
        # 赠送发起后、确认前的窗口内记录可能刚被分配：接受阶段必须按最新状态拦截，
        # 不让分配记录经赠送易主。
        self.pending.get_pending.return_value = {'giver_id': 'u1', 'created_at': 0}
        await self.gift_accept(self.bot, self.ev, 'wife')
        self.pending.clear_pending.assert_called_once()
        self.saved.assert_not_awaited()
        self.assertIn('assigned_by', self.context['wives']['u1'])
        self.messages.assert_awaited_once_with(self.bot, '这个老婆是主人分配的，不能送出去，赠送已失效~')


if __name__ == '__main__':
    unittest.main()
