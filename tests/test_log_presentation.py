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


def test_play_duration_survives_concise_filter_without_other_parameters():
    assert concise_message('出牌点击已观察 耗时:2.55秒', 'INFO') == '出牌点击已观察（耗时 2.55 秒）'
    assert concise_message('出牌点击已观察 耗时:0.00秒', 'INFO') == '出牌点击已观察（耗时 0.00 秒）'
    assert concise_message('出牌点击已观察 耗时:2.55秒', 'DEBUG') is None
    assert concise_message('卡牌识别，耗时:2.55秒，roi=[1,2,3,4]', 'INFO') == '卡牌识别'


def test_play_duration_reaches_console_in_both_modes(tmp_path, monkeypatch, capsys):
    from utils.logger import setup_logger
    monkeypatch.chdir(tmp_path)
    (tmp_path/'config').mkdir()
    for detailed in (False, True):
        (tmp_path/'config/card_lists.json').write_text(json.dumps({'detailedLogsEnabled': detailed}), encoding='utf8')
        logger = setup_logger(str(tmp_path/'logs'))
        logger.info('出牌点击已观察 耗时:2.55秒')
        logger.complete()
        output = capsys.readouterr().err
        visible = [line for line in output.splitlines() if not line.startswith('tasklog:')]
        assert len(visible) == 1 and '2.55' in visible[0] and '耗时' in visible[0]
        logger.remove()


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


def test_watchdog_stream_is_identical_in_concise_and_detailed_modes(tmp_path, monkeypatch, capsys):
    from utils.logger import setup_logger
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'config').mkdir()
    policy = tmp_path / 'config/card_lists.json'
    raw = 'ProduceCardsAuto remaining=7 card=届いて！, confidence=0.932'
    observations = []
    for detailed in (False, True):
        policy.write_text(json.dumps({'detailedLogsEnabled': detailed}), encoding='utf8')
        logger = setup_logger(str(tmp_path / 'logs'))
        logger.info(raw)
        logger.complete()
        output = capsys.readouterr().err
        observations.append([json.loads(line[8:]) for line in output.splitlines() if line.startswith('tasklog:')])
        logger.remove()
    assert observations == [[raw], [raw]]


def test_watchdog_stream_preserves_multiline_as_one_json_record(tmp_path, monkeypatch, capsys):
    from utils.logger import setup_logger
    monkeypatch.chdir(tmp_path)
    logger = setup_logger(str(tmp_path / 'logs'))
    message = '等待确认\n卡牌「届いて！」'
    logger.warning(message)
    logger.complete()
    records = [json.loads(line[8:]) for line in capsys.readouterr().err.splitlines() if line.startswith('tasklog:')]
    assert records == [message]
    logger.remove()
