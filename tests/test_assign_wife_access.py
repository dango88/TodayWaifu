import ast
import unittest
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UPLOAD_ACCESS_PATH = ROOT / 'TodayWaifu' / 'upload_access.py'


# 以文件路径而非包名加载 upload_access：该模块是纯函数实现，直接加载可绕开
# 导入 TodayWaifu 包时对 gsuid_core 的重依赖，使权限判定的单测无需框架环境。
def _load_upload_access():
    spec = importlib.util.spec_from_file_location('todaywaifu_assign_upload_access', UPLOAD_ACCESS_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError('cannot load upload_access module')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding='utf-8-sig')


def _function_source(relative: str, name: str) -> str:
    tree = ast.parse(_source(relative))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.unparse(node)
    raise AssertionError(f'{relative} 里没有 {name}')


class AssignWifeAccessTests(unittest.TestCase):
    """分配老婆的放行条件由「仅主人」放宽为「主人或白名单」，且不得回退。"""

    # 以下四例锁定 can_use_whitelisted_feature 的判定边界。该函数同时承载上传图片与
    # 分配老婆两条链路，任一侧放宽都会同时改变另一侧的可达用户范围，故边界须显式固定。
    def test_whitelisted_user_passes_generic_gate(self) -> None:
        access = _load_upload_access()
        self.assertTrue(access.can_use_whitelisted_feature('10001', [], ['10001']))

    def test_master_passes_generic_gate_without_whitelist(self) -> None:
        access = _load_upload_access()
        self.assertTrue(access.can_use_whitelisted_feature('10001', ['10001'], []))

    def test_unlisted_user_is_rejected(self) -> None:
        access = _load_upload_access()
        self.assertFalse(access.can_use_whitelisted_feature('10001', ['20002'], ['30003']))

    def test_string_whitelist_accepts_common_separators(self) -> None:
        access = _load_upload_access()
        self.assertTrue(access.can_use_whitelisted_feature('10002', [], '10001, 10002\n10003'))

    # 服务权限（user_pm / sv.pm）与白名单是两条独立放行路径。数值越小权限越高，
    # 比较方向写反会让低权限用户通过、高权限用户被拒，且不易在日常使用中暴露。
    def test_service_permission_level_allows_authorized_user(self) -> None:
        access = _load_upload_access()
        self.assertTrue(access.can_use_pm_or_whitelisted_feature('10001', 3, 3, [], []))

    def test_service_permission_level_rejects_lower_priority_user(self) -> None:
        access = _load_upload_access()
        self.assertFalse(access.can_use_pm_or_whitelisted_feature('10001', 6, 3, [], []))

    def test_whitelist_still_overrides_service_permission_level(self) -> None:
        access = _load_upload_access()
        self.assertTrue(access.can_use_pm_or_whitelisted_feature('10001', 6, 3, [], ['10001']))

    # 以下三例以源码文本为断言对象（而非行为）：分配入口需经统一鉴权门，且
    # 白名单配置名必须与配置默认值中的键一致，任一改名都会静默失效。
    def test_assign_gate_uses_assign_whitelist(self) -> None:
        gate = _function_source('TodayWaifu/shared.py', '_can_assign_wife')
        self.assertIn('_is_master(ev)', gate)
        self.assertIn('DailyWifeAssignWhitelist', gate)
        # ev.user_pm + assign_wife_sv.pm 是 98446b8 补入的服务权限比较；缺失时
        # 达到该服务权限的用户会被判为无权限，即该提交所修的线上问题。
        self.assertIn('ev.user_pm', gate)
        self.assertIn('assign_wife_sv.pm', gate)

    def test_assign_gate_is_exported_for_submodules(self) -> None:
        self.assertIn("'_can_assign_wife'", _source('TodayWaifu/shared.py'))

    def test_assign_handler_uses_whitelist_gate(self) -> None:
        handler = _function_source('TodayWaifu/daily.py', '_send_assign_daily')
        self.assertIn('_can_assign_wife(ev)', handler)
        # 处理器内不得再自行判断主人身份：绕过统一门会出现「进了函数但判定口径不同」
        # 的双重鉴权，白名单用户在此被拒则前面放行的白名单配置形同虚设。
        self.assertNotIn('_is_master(ev)', handler)
        self.assertIn('白名单', handler)

    def test_assign_sv_allows_everyone_and_relies_on_gate(self) -> None:
        tree = ast.parse(_source('TodayWaifu/shared.py'))
        for node in tree.body:
            if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
                continue
            if not any(isinstance(t, ast.Name) and t.id == 'assign_wife_sv' for t in node.targets):
                continue
            keywords = {keyword.arg: ast.unparse(keyword.value) for keyword in node.value.keywords}
            self.assertNotIn('pm', keywords, '分配老婆的 SV 不能再锁 pm（否则白名单用户到不了处理器）')
            return
        raise AssertionError('没有找到 assign_wife_sv 的定义')

    def test_assign_whitelist_config_keeps_default_empty(self) -> None:
        # 默认值必须为空：该项一旦默认放行，升级插件即等于对全部用户开放分配权限。
        tree = ast.parse(_source('config_default.py'))
        for node in tree.body:
            if not isinstance(node, ast.AnnAssign) or not isinstance(node.value, ast.Dict):
                continue
            if not isinstance(node.target, ast.Name) or node.target.id != 'CONFIG_DEFAULT':
                continue
            for key, value in zip(node.value.keys, node.value.values):
                if isinstance(key, ast.Constant) and key.value == 'DailyWifeAssignWhitelist':
                    expr = ast.unparse(value)
                    self.assertIn('GsListStrConfig', expr)
                    self.assertTrue(expr.endswith('[])'), expr)
                    return
        raise AssertionError('CONFIG_DEFAULT 里没有 DailyWifeAssignWhitelist')


if __name__ == '__main__':
    unittest.main()
