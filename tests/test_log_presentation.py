import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'agent'))
from utils.log_presentation import concise_message, detailed_logs_enabled


def test_simple_actions_remain_visible():
    assert concise_message('使用卡牌：届いて！', 'INFO') == '使用卡牌：届いて！'
    assert concise_message('任务已完成', 'SUCCESS') == '任务已完成'


def test_diagnostics_do_not_leak_into_concise_output():
    assert concise_message('ProduceCardsAuto remaining=7', 'DEBUG') is None
    assert concise_message('出牌失败: roi=[1,2,3,4]', 'ERROR') == '操作未完成，请查看详细日志。'
    assert concise_message('已选择卡牌，置信度: 0.95', 'INFO') == '已选择卡牌'


def test_switch_is_live_and_defaults_off(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert not detailed_logs_enabled()
    (tmp_path/'config').mkdir()
    config=tmp_path/'config/card_lists.json'
    config.write_text(json.dumps({'debugDiagnosticsEnabled':True}),encoding='utf8')
    assert not detailed_logs_enabled()
    config.write_text(json.dumps({'detailedLogsEnabled':True}),encoding='utf8')
    assert detailed_logs_enabled()
    config.write_text('{broken',encoding='utf8')
    assert not detailed_logs_enabled()


def test_exception_details_stay_in_file_unless_enabled(tmp_path, monkeypatch, capsys):
    from utils.logger import setup_logger
    monkeypatch.chdir(tmp_path)
    logger=setup_logger(str(tmp_path/'logs'))
    try:
        1/0
    except ZeroDivisionError:
        logger.exception('出牌失败')
    logger.complete()
    console=capsys.readouterr().err
    assert '出牌失败' in console
    assert 'ZeroDivisionError' not in console and 'Traceback' not in console
    assert 'ZeroDivisionError' in next((tmp_path/'logs').glob('*.log')).read_text('utf8')
    (tmp_path/'config').mkdir()
    (tmp_path/'config/card_lists.json').write_text('{"detailedLogsEnabled":true}',encoding='utf8')
    logger.debug('remaining=7')
    assert 'remaining=7' in capsys.readouterr().err
    logger.remove()
