#!/usr/bin/env python3
"""
五大游资诊股 Web 应用 v2
输入股票代码 → 实时行情 + 五大游资框架深度分析
数据：腾讯行情 + pytdx 5档 + 东方财富龙虎榜 + 技术指标
"""

import sys, os, re, json, math
from flask import Flask, request, render_template, jsonify
from datetime import datetime, timedelta

# ── 工具函数 ──────────────────────────────────────────────────────────────

def safe_float(val, default=0.0):
    try:
        return float(val)
    except (TypeError, ValueError):
        return default

def safe_div(a, b, default=0.0):
    try:
        return round(a / b, 4) if b != 0 else default
    except:
        return default

# ── 数据获取层 ────────────────────────────────────────────────────────────

def get_realtime(stock_code: str) -> dict:
    """腾讯行情接口，沪深+北交所通用"""
    code = stock_code.strip().upper()
    if code.startswith('SH') or code.startswith('SZ'):
        prefix = code[:2].lower()
        code = code[2:]
    elif re.match(r'^\d{6}$', code):
        # 纯6位数字：智能判断沪深 vs 北交所 vs 沪市
        if code.startswith(('4', '8', '9')):
            # 4=新三板 8/9=北交所
            prefix = 'bj'
        elif code.startswith(('0', '3')):
            prefix = 'sz'
        else:
            prefix = 'sh'
    elif code.startswith('BJ'):
        if len(code) == 6:
            code = 'BJ' + code
        prefix = 'bj'
    else:
        prefix = 'sh'

    url = f"https://qt.gtimg.cn/q={prefix}{code}"
    try:
        import requests
        resp = requests.get(url, headers={'Referer': 'https://finance.qq.com'}, timeout=5)
        resp.encoding = 'gbk'
        text = resp.text
        if 'null' in text or len(text) < 50:
            return {}
        eq_pos = text.find('=')
        if eq_pos < 0:
            return {}
        raw = text[eq_pos + 1:].strip()
        if raw.startswith('"') and raw.endswith('"'):
            raw = raw[1:-1]
        parts = raw.split('~')
        if len(parts) < 45:
            return {}
        is_bj = prefix == 'bj'
        price = safe_float(parts[3])
        high = safe_float(parts[33])
        low = safe_float(parts[34])
        prev_close = safe_float(parts[4])
        open_ = safe_float(parts[5])
        vol = safe_float(parts[36])  # 手
        amount = safe_float(parts[37])  # 元
        change = safe_float(parts[32])
        change_pct = safe_float(parts[32])  # 涨跌幅%
        # 5档行情（腾讯接口）
        bid1_vol = safe_float(parts[36+1]) if len(parts) > 36+1 else 0  # 买1量
        ask1_vol = safe_float(parts[38+1]) if len(parts) > 38+1 else 0  # 卖1量
        amp = round((high - low) / low * 100, 2) if low > 0 else 0
        turnover = round(amount / 100000000, 2) if amount > 0 else 0  # 亿
        pe = safe_float(parts[39]) if len(parts) > 39 else 0
        pb = safe_float(parts[46]) if len(parts) > 46 else 0
        total_mv = safe_float(parts[44]) if len(parts) > 44 else 0  # 总市值
        flow_mv = safe_float(parts[45]) if len(parts) > 45 else 0  # 流通市值

        return {
            'name': parts[1],
            'code': parts[2],
            'price': price,
            'prev_close': prev_close,
            'open': open_,
            'high': high,
            'low': low,
            'volume': int(vol),
            'amount': amount,
            'turnover': turnover,
            'change': change,
            'change_pct': change_pct,
            'amp': amp,
            'pe': pe,
            'pb': pb,
            'total_mv': round(total_mv / 100000000, 2) if total_mv > 0 else 0,
            'flow_mv': round(flow_mv / 100000000, 2) if flow_mv > 0 else 0,
            'bid1_vol': int(bid1_vol),
            'ask1_vol': int(ask1_vol),
        }
    except Exception:
        return {}


def get_kline_pytdx(stock_code: str, days: int = 60) -> list:
    """pytdx暂时不可用，返回空列表"""
    return []


def calc_ma(kline: list, n: int) -> float:
    """计算MA均线"""
    if len(kline) < n:
        return 0.0
    prices = [bar['close'] for bar in kline[-n:]]
    return round(sum(prices) / n, 3)


def calc_vol_ratio(kline: list, days: int = 5) -> float:
    """量比（近N日平均成交量 / 上N日平均成交量）"""
    if len(kline) < days * 2:
        return 1.0
    recent_vol = sum(kline[-days:][i]['vol'] for i in range(days)) / days
    prev_vol = sum(kline[-days*2:-days][i]['vol'] for i in range(days)) / days
    return round(recent_vol / prev_vol, 2) if prev_vol > 0 else 1.0


def get_dragon_tiger(stock_code: str) -> dict:
    """东方财富龙虎榜数据"""
    code = stock_code.strip().upper()
    if code.startswith(('SH', 'SZ', 'BJ')):
        code = code[-6:]
    try:
        import requests
        # 东方财富龙虎榜明细
        url = f"https://data.eastmoney.com/stock/lhb/{code.lower()}.html"
        # 用数据接口
        data_url = f"https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_DRAGON_TIGER_STOCK&columns=ALL&filter=(SECUCODE%3D%22{code}%22)&pageNumber=1&pageSize=5&sortTypes=-1&sortColumns=TRADE_DATE&source=WEB"
        resp = requests.get(data_url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://data.eastmoney.com/'
        }, timeout=8)
        if resp.status_code != 200:
            return {}
        js = resp.json()
        result = js.get('result', {})
        if not result:
            return {}
        items = result.get('data', [])
        if not items:
            return {}
        latest = items[0]
        return {
            'date': latest.get('TRADE_DATE', '')[:10] if latest.get('TRADE_DATE') else '',
            'reason': latest.get('CHANGE_REASON', ''),
            'close_price': safe_float(latest.get('CLOSE_PRICE', 0)),
            'change_pct': safe_float(latest.get('CHANGE_RATE', 0)),
            'buy_seats': [latest.get(f'BUY_SEAT{i}', '') for i in range(1, 6) if latest.get(f'BUY_SEAT{i}')],
            'sell_seats': [latest.get(f'SELL_SEAT{i}', '') for i in range(1, 6) if latest.get(f'SELL_SEAT{i}')],
            'netBuy': safe_float(latest.get('NET_BUY', 0)),
            'totalBuy': safe_float(latest.get('TOTAL_BUY', 0)),
            'totalSell': safe_float(latest.get('TOTAL_SELL', 0)),
        }
    except Exception:
        return {}


def get_zt_status(data: dict) -> dict:
    """涨跌停状态"""
    change = data.get('change_pct', 0)
    if change >= 9.8:
        status = '🔴 涨停'
    elif change <= -9.8:
        status = '🟢 跌停'
    elif change >= 5:
        status = '⚡ 强势'
    elif change <= -5:
        status = '⚡ 弱势'
    else:
        status = '➖ 震荡'
    return {'status': status, 'change_pct': change}


# ── 五大游资框架 ─────────────────────────────────────────────────────────

def analyze_chenxiaoqun(data: dict, kline: list, vol_ratio: float) -> dict:
    """陈小群：情绪周期 + 合力框架"""
    change = data.get('change_pct', 0)
    amp = data.get('amp', 0)
    price = data.get('price', 0)
    turnover = data.get('turnover', 0)
    bid1 = data.get('bid1_vol', 0)
    ask1 = data.get('ask1_vol', 0)

    # 量价信号
    ma5 = calc_ma(kline, 5) if kline else 0
    ma10 = calc_ma(kline, 10) if kline else 0
    price_above_ma5 = price > ma5 if ma5 else None

    # 涨停封单强度
    seal_strength = '强' if bid1 > 50000 else '中' if bid1 > 10000 else '弱'

    if change >= 9.8:
        verdict = f'情绪高潮期，涨停板可博弈，封单{seal_strength}'
        suggestion = f'轻仓试探，MA5={ma5}上方持有，止损-3%'
        level = '🟢'
    elif change >= 5:
        verdict = f'情绪升温区，板块带动明显，量比{vol_ratio}说明资金活跃'
        suggestion = '回调均价买入，不追高' if not price_above_ma5 else '价格已高于MA5，谨慎追入'
        level = '🟡'
    elif change >= 2:
        verdict = '情绪回暖，趋势形成中，耐心等待买点'
        suggestion = f'等缩量回踩MA{10 if ma10 else 10}均线买入'
        level = '🔵'
    elif change >= -2:
        verdict = '情绪平淡，横盘整理，观望为主'
        suggestion = '不参与，等待方向明确'
        level = '⚪'
    elif change >= -5:
        verdict = '情绪退潮，下降趋势，不抄底'
        suggestion = '止损或空仓'
        level = '🟠'
    else:
        verdict = '情绪冰点，恐慌杀跌，远离'
        suggestion = '忍住，不接飞刀'
        level = '🔴'
    return {'trader': '陈小群', 'verdict': verdict, 'suggestion': suggestion, 'level': level}


def analyze_chaoguyangjia(data: dict, kline: list, vol_ratio: float) -> dict:
    """炒股养家：大局观心法"""
    change = data.get('change_pct', 0)
    high = data.get('high', 0)
    low = data.get('low', 0)
    price = data.get('price', 0)
    open_ = data.get('open', 0)
    turnover = data.get('turnover', 0)
    total_mv = data.get('total_mv', 0)

    today_range = round((high - low) / low * 100, 2) if low > 0 else 0
    # 养家心法核心：大盘氛围 + 龙头定位 + 资金性质
    ma5 = calc_ma(kline, 5) if kline else 0
    ma20 = calc_ma(kline, 20) if kline else 0

    if change >= 9.8:
        verdict = f'市场焦点，龙头地位确认，大局观：持有至尾盘'
        suggestion = '早盘炸板可出，缩量封板持有'
        level = '🟢'
    elif today_range > 10:
        verdict = f'振幅过大({today_range}%)，多空分歧严重'
        suggestion = '新手观望，不频繁操作'
        level = '🟡'
    elif change >= 5:
        verdict = f'板块联动，赚钱效应扩散，大局观支持持仓'
        suggestion = '顺势而为，不轻易下车'
        level = '🔵'
    elif change >= 2:
        verdict = f'市值{total_mv}亿，流通性{turnover}亿，大局观：等待催化'
        suggestion = f'MA5={ma5}，MA20={ma20}，等待方向'
        level = '🔵'
    elif change >= -2:
        verdict = f'横盘({today_range}%振幅)，市场观望，控仓等待'
        suggestion = '控制仓位，不盲目加仓'
        level = '⚪'
    else:
        verdict = '市场情绪差，大局观提醒：防守为主'
        suggestion = '减仓或空仓'
        level = '🟠'
    return {'trader': '炒股养家', 'verdict': verdict, 'suggestion': suggestion, 'level': level}


def analyze_asking(data: dict, kline: list, vol_ratio: float, tiger: dict) -> dict:
    """asking：龙头战法"""
    change = data.get('change_pct', 0)
    amp = data.get('amp', 0)
    price = data.get('price', 0)
    bid1 = data.get('bid1_vol', 0)

    ma5 = calc_ma(kline, 5) if kline else 0
    ma10 = calc_ma(kline, 10) if kline else 0

    # 龙虎榜信号
    has_tiger = bool(tiger.get('date'))
    net_buy = tiger.get('netBuy', 0) if tiger else 0

    if change >= 9.8:
        if has_tiger:
            verdict = f'涨停+龙虎榜(净买入{net_buy}万)，asking：龙头战法核心标的'
        else:
            verdict = '涨停板，龙头战法核心标的，需确认板块地位'
        suggestion = '首封可打，二封加仓，炸板必出'
        level = '🟢'
    elif change >= 7:
        verdict = '冲击涨停，龙头气质显现'
        suggestion = '高位震荡若不破均价，可持有'
        level = '🟡'
    elif change >= 5:
        verdict = f'强势股，量比{vol_ratio}，asking：强者恒强'
        suggestion = '跟风不足时卖出'
        level = '🔵'
    elif change >= 2:
        verdict = f'趋势股，asking：耐心等待确定性机会'
        suggestion = f'回调MA{10 if ma10 else 10}再考虑买入'
        level = '🔵'
    else:
        verdict = '弱势，asking：不在下跌中买股'
        suggestion = '空仓等待'
        level = '🔴'
    return {'trader': 'asking', 'verdict': verdict, 'suggestion': suggestion, 'level': level}


def analyze_chanlun(data: dict, kline: list) -> dict:
    """缠中说禅：走势终完美（简化版，基于均线系统）"""
    change = data.get('change_pct', 0)
    price = data.get('price', 0)
    high = data.get('high', 0)
    low = data.get('low', 0)

    if not kline or len(kline) < 20:
        return {'trader': '缠中说禅', 'verdict': '数据不足，无法完整分析', 'suggestion': '等待K线数据', 'level': '⚪'}

    ma5 = calc_ma(kline, 5)
    ma10 = calc_ma(kline, 10)
    ma20 = calc_ma(kline, 20)
    ma30 = calc_ma(kline, 30) if len(kline) >= 30 else 0

    # 均线多头排列
    bull_alignment = ma5 > ma10 > ma20 if ma20 else ma5 > ma10
    bear_alignment = ma5 < ma10 < ma20 if ma20 else ma5 < ma10

    # 价位在中枢位置（简化：看价格与均线关系）
    above_all = price > ma5 > ma10 > ma20 if ma20 else price > ma5 > ma10

    if change >= 9.8:
        verdict = '涨停，一买点确认后延续，走势终完美已实现'
        suggestion = '第二买点不破涨停板可入'
        level = '🟢'
    elif change >= 5:
        if above_all:
            verdict = f'上升走势延续，中枢上方运行，均线多头（MA5>{ma5}）'
        else:
            verdict = '上升走势，但需观察能否重回中枢'
        suggestion = '持有或回调不破MA5加仓'
        level = '🟡'
    elif change >= 0:
        if bull_alignment:
            verdict = f'横盘震荡构建上涨中枢，均线多头：MA5={ma5} MA10={ma10}'
        else:
            verdict = f'横盘震荡，均线纠缠，耐心等待三买'
        suggestion = '不追，等次级别回调企稳'
        level = '🔵'
    else:
        if bear_alignment:
            verdict = f'下跌走势延续，缠论：在下跌走势结束前不抄底'
            suggestion = '等待一买或二买信号'
        else:
            verdict = '下跌走势，需观察是否形成底部中枢'
            suggestion = '等待震荡企稳'
        level = '🔴'
    return {'trader': '缠中说禅', 'verdict': verdict, 'suggestion': suggestion, 'level': level}


def analyze_hujialou(data: dict, kline: list, tiger: dict) -> dict:
    """呼家楼：政策驱动 + 龙头重仓"""
    change = data.get('change_pct', 0)
    price = data.get('price', 0)
    total_mv = data.get('total_mv', 0)

    # 呼家楼偏好：大盘股 + 政策催化 + 多席位合力
    is_large_cap = total_mv > 100  # 亿
    has_tiger = bool(tiger.get('date'))

    if change >= 9.8:
        verdict = f'政策催化+龙头确认，{"大盘" if is_large_cap else "小盘"}股，呼家楼：重仓出击'
        suggestion = '核心龙头满仓，止损+8%'
        level = '🟢'
    elif change >= 7:
        verdict = f'政策驱动明显，{"大盘" if is_large_cap else "小盘"}股，呼家楼：顺势重仓'
        suggestion = '回调站稳均线可加仓'
        level = '🟡'
    elif change >= 5:
        verdict = f'板块轮动机会，{"大盘" if is_large_cap else "小盘"}股，呼家楼：轻仓试探'
        suggestion = '不超过三成仓'
        level = '🔵'
    elif change >= 0:
        verdict = '无明显催化，呼家楼：等待政策信号'
        suggestion = '不操作，等风来'
        level = '⚪'
    else:
        verdict = '逆政策方向，呼家楼：政策底未明，不参与'
        suggestion = '空仓'
        level = '🔴'
    return {'trader': '呼家楼', 'verdict': verdict, 'suggestion': suggestion, 'level': level}


def full_analysis(stock_code: str, include_kline: bool = False) -> dict:
    """综合分析入口"""
    data = get_realtime(stock_code)
    if not data:
        return {'error': '无法获取行情数据，请检查股票代码'}
    if data.get('price', 0) == 0:
        return {'error': '股票代码无效'}

    zt = get_zt_status(data)
    data.update(zt)

    kline = []
    vol_ratio = 1.0
    tiger = {}

    if include_kline:
        # K线数据（可能较慢）
        kline = get_kline_pytdx(stock_code, days=60)
        vol_ratio = calc_vol_ratio(kline) if kline else 1.0
        tiger = get_dragon_tiger(stock_code)
    else:
        # 仅用当日数据快速返回
        vol_ratio = 1.0

    # 技术指标
    ma5 = calc_ma(kline, 5) if kline else 0
    ma10 = calc_ma(kline, 10) if kline else 0
    ma20 = calc_ma(kline, 20) if kline else 0

    # 五框架分析
    analysts = [
        analyze_chenxiaoqun(data, kline, vol_ratio),
        analyze_chaoguyangjia(data, kline, vol_ratio),
        analyze_asking(data, kline, vol_ratio, tiger),
        analyze_chanlun(data, kline),
        analyze_hujialou(data, kline, tiger),
    ]

    # 打分
    scores_map = {'🟢': 5, '🟡': 3, '🔵': 2, '⚪': 1, '🟠': 0, '🔴': -1}
    total = sum(scores_map.get(a['level'], 0) for a in analysts)
    bullish = sum(1 for a in analysts if a['level'] in ('🟢', '🟡'))
    bearish = sum(1 for a in analysts if a['level'] in ('🟠', '🔴'))

    if total >= 20:
        overall = '🥇 强烈推荐买入'
        action = '建议关注，适时介入'
    elif total >= 12:
        overall = '🟡 谨慎关注'
        action = '轻仓试探，严格止损'
    elif total >= 5:
        overall = '⚪ 观望'
        action = '等待方向明确'
    else:
        overall = '🔴 建议回避'
        action = '控制风险，不参与'

    result = {
        'data': data,
        'analysts': analysts,
        'overall': overall,
        'action': action,
        'score': total,
        'score_max': 25,
        'bullish': bullish,
        'bearish': bearish,
        'signals': {'vol_ratio': vol_ratio},
    }
    if include_kline:
        result['kline'] = {
            'ma5': ma5, 'ma10': ma10, 'ma20': ma20,
            'count': len(kline),
        }
        result['dragon_tiger'] = tiger if tiger else None
    return result


# ── Flask App ─────────────────────────────────────────────────────────────

app = Flask(__name__)


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/test')
def api_test():
    code = request.args.get('code', '300413')
    d = get_realtime(code)
    return jsonify({'raw': d, 'bool': bool(d), 'code': code})


@app.route('/api/analyze')
def api_analyze():
    code = request.args.get('code', '')
    full = request.args.get('full', '0') == '1'
    result = full_analysis(code, include_kline=full)
    return jsonify(result)


@app.route('/api/kline')
def api_kline():
    """K线数据接口"""
    code = request.args.get('code', '')
    days = int(request.args.get('days', 60))
    kline = get_kline_pytdx(code, days=days)
    if not kline:
        return jsonify({'error': '暂无K线数据'})
    return jsonify({'code': code, 'kline': kline})


@app.route('/api/tiger')
def api_tiger():
    """龙虎榜数据接口"""
    code = request.args.get('code', '')
    tiger = get_dragon_tiger(code)
    return jsonify({'code': code, 'dragon_tiger': tiger if tiger else {'note': '近期无龙虎榜数据'}})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=7860, debug=False)
