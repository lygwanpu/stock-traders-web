#!/usr/bin/env python3
"""
五大游资诊股 Web 应用 v3
输入股票代码 → 实时行情 + 大盘分析 + 五大游资框架深度分析
数据：腾讯行情 + 东方财富大盘资金流 + 技术指标
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

# ── 大盘数据层 ────────────────────────────────────────────────────────────

def get_realtime_index() -> dict:
    """获取大盘实时行情（上证/深证/创业板/科创50）"""
    indices = [
        ('sh000001', '上证指数'),
        ('sz399001', '深证成指'),
        ('sz399006', '创业板指'),
        ('sh000688', '科创50'),
    ]
    results = {}
    for code, name in indices:
        url = f"https://qt.gtimg.cn/q={code}"
        try:
            import requests
            resp = requests.get(url, headers={'Referer': 'https://finance.qq.com'}, timeout=5)
            resp.encoding = 'gbk'
            text = resp.text
            eq_pos = text.find('=')
            if eq_pos < 0:
                continue
            raw = text[eq_pos + 1:].strip().strip('"')
            parts = raw.split('~')
            if len(parts) < 45:
                continue
            price = safe_float(parts[3])
            prev = safe_float(parts[4])
            high = safe_float(parts[33])
            low = safe_float(parts[34])
            chg = safe_float(parts[32])
            chg_pct = (price - prev) / prev * 100 if prev > 0 else 0
            amp = (high - low) / low * 100 if low > 0 else 0
            vol = safe_float(parts[36])   # 手
            amount = safe_float(parts[37]) # 元
            turnover = round(amount / 100000000, 2) if amount > 0 else 0
            results[code] = {
                'name': name,
                'code': code,
                'price': price,
                'prev_close': prev,
                'high': high,
                'low': low,
                'change': chg,
                'change_pct': round(chg_pct, 2),
                'amp': round(amp, 2),
                'volume': int(vol),
                'turnover': turnover,  # 亿
            }
        except Exception:
            continue
    return results


def get_market_breadth() -> dict:
    """东方财富大盘涨跌家数（市场宽度）"""
    try:
        import requests
        # 沪深大盘宽度：上涨/下跌/平盘家数
        url = "https://push2.eastmoney.com/api/qt/stock/get"
        params = {
            'secid': '1.000001',
            'fields': 'f43,f169,f170',
            'ut': 'fa5fd1943c7b386f172d6893dbfba10b',
        }
        resp = requests.get(url, params=params, headers={
            'User-Agent': 'Mozilla/5.0',
            'Referer': 'https://quote.eastmoney.com/',
        }, timeout=5)
        # 如果接口失败，用涨跌家数近似
        broad_url = "https://push2.eastmoney.com/api/qt/ulist/get"
        broad_params = {
            'fltt': '2',
            'invt': '2',
            'fields': 'f12,f14,f3',
            'pn': '1',
            'pz': '20',
            'fs': 'm:0+t:6,m:0+t:13,m:1+t:2,m:1+t:23',
        }
        br = requests.get(broad_url, params=broad_params, headers={
            'User-Agent': 'Mozilla/5.0',
            'Referer': 'https://quote.eastmoney.com/',
        }, timeout=5)
        # 简化：返回None让调用方用指数涨跌幅近似
        return {}
    except Exception:
        return {}


def get_market_sentiment() -> dict:
    """综合大盘情绪判断"""
    indices = get_realtime_index()
    if not indices:
        return {'sentiment': '⚪ 无法获取大盘数据', 'level': 0, 'indices': {}}

    sh = indices.get('sh000001', {})
    sz = indices.get('sz399001', {})
    cy = indices.get('sz399006', {})

    sh_chg = sh.get('change_pct', 0)
    sz_chg = sz.get('change_pct', 0)
    cy_chg = cy.get('change_pct', 0)

    # 平均涨幅
    all_chg = [sh_chg, sz_chg]
    if cy:
        all_chg.append(cy_chg)
    avg_chg = sum(all_chg) / len(all_chg)

    # 情绪判断
    if avg_chg >= 2:
        sentiment = '🟢 强势上涨'
        level = 3
    elif avg_chg >= 0.5:
        sentiment = '🔵 温和上涨'
        level = 2
    elif avg_chg >= -0.5:
        sentiment = '⚪ 震荡整理'
        level = 1
    elif avg_chg >= -2:
        sentiment = '🟠 温和下跌'
        level = -1
    else:
        sentiment = '🔴 恐慌下跌'
        level = -2

    # 赚钱效应
    rising = sum(1 for c in all_chg if c > 0)
    fallng = sum(1 for c in all_chg if c < 0)

    return {
        'sentiment': sentiment,
        'level': level,
        'avg_change_pct': round(avg_chg, 2),
        'rising_count': rising,
        'falling_count': fallng,
        'indices': {
            '上证指数': sh,
            '深证成指': sz,
            '创业板指': cy if cy else None,
        }
    }


# ── 个股数据层 ────────────────────────────────────────────────────────────

def get_realtime(stock_code: str) -> dict:
    """腾讯行情接口，沪深+北交所通用"""
    code = stock_code.strip().upper()
    if code.startswith('SH') or code.startswith('SZ'):
        prefix = code[:2].lower()
        code = code[2:]
    elif re.match(r'^\d{6}$', code):
        if code.startswith(('4', '8', '9')):
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
        vol = safe_float(parts[36])
        amount = safe_float(parts[37])
        change = safe_float(parts[32])
        change_pct = safe_float(parts[32])
        bid1_vol = safe_float(parts[36+1]) if len(parts) > 36+1 else 0
        ask1_vol = safe_float(parts[38+1]) if len(parts) > 38+1 else 0
        amp = round((high - low) / low * 100, 2) if low > 0 else 0
        turnover = round(amount / 100000000, 2) if amount > 0 else 0
        pe = safe_float(parts[39]) if len(parts) > 39 else 0
        pb = safe_float(parts[46]) if len(parts) > 46 else 0
        total_mv = safe_float(parts[44]) if len(parts) > 44 else 0
        flow_mv = safe_float(parts[45]) if len(parts) > 45 else 0

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
    """K线数据（暂时不可用）"""
    return []


def calc_ma(kline: list, n: int) -> float:
    if len(kline) < n:
        return 0.0
    prices = [bar['close'] for bar in kline[-n:]]
    return round(sum(prices) / n, 3)


def calc_vol_ratio(kline: list, days: int = 5) -> float:
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


# ── 大盘情绪对个股的影响 ─────────────────────────────────────────────────

def get_market_influence(market: dict, stock_chg: float) -> dict:
    """
    分析大盘与个股的联动关系
    market: get_market_sentiment() 返回的字典
    stock_chg: 个股涨跌幅
    返回：大盘助力/阻力说明
    """
    level = market.get('level', 0)
    sentiment = market.get('sentiment', '⚪')
    avg_chg = market.get('avg_change_pct', 0)

    stock_dir = 1 if stock_chg > 0 else -1 if stock_chg < 0 else 0
    mkt_dir = 1 if avg_chg > 0.3 else -1 if avg_chg < -0.3 else 0

    if level >= 2 and stock_dir == 1:
        influence = '大盘助力 🎯 顺势而为'
        bonus = '+2'
    elif level >= 2 and stock_dir == -1:
        influence = '大盘强但个股逆势 ⚠️ 谨慎'
        bonus = '-1'
    elif level <= -1 and stock_dir == -1:
        influence = '大盘拖累 📉 系统性风险'
        bonus = '-2'
    elif level <= -1 and stock_dir == 1:
        influence = '逆势走强 ✨ 独立行情需验证'
        bonus = '+1'
    elif level == 1 and abs(stock_chg) < 1:
        influence = '大盘震荡 个股随波逐流'
        bonus = '0'
    else:
        influence = '大盘中性'
        bonus = '0'

    return {'influence': influence, 'bonus': bonus, 'market_sentiment': sentiment}


# ── 五大游资框架（加入大盘参数）──────────────────────────────────────────

def analyze_chenxiaoqun(data: dict, kline: list, vol_ratio: float, market: dict) -> dict:
    """陈小群：情绪周期 + 合力框架"""
    change = data.get('change_pct', 0)
    amp = data.get('amp', 0)
    price = data.get('price', 0)
    turnover = data.get('turnover', 0)
    bid1 = data.get('bid1_vol', 0)
    mkt_influence = get_market_influence(market, change)
    mkt_desc = f"「大盘{mkt_influence['market_sentiment']}·{mkt_influence['influence']}」"

    ma5 = calc_ma(kline, 5) if kline else 0
    ma10 = calc_ma(kline, 10) if kline else 0
    seal_strength = '强' if bid1 > 50000 else '中' if bid1 > 10000 else '弱'

    if change >= 9.8:
        verdict = f'{mkt_desc} | 情绪高潮，涨停板可博弈，封单{seal_strength}'
        suggestion = f'轻仓试探，MA5={ma5}上方持有，止损-3%'
        level = '🟢'
    elif change >= 5:
        verdict = f'{mkt_desc} | 情绪升温，板块带动明显，量比{vol_ratio}'
        suggestion = '回调均价买入，不追高' if not (ma5 and price > ma5) else '已高于MA5，谨慎追入'
        level = '🟡'
    elif change >= 2:
        verdict = f'{mkt_desc} | 情绪回暖，趋势形成中'
        suggestion = f'等缩量回踩MA10({ma10})买入'
        level = '🔵'
    elif change >= -2:
        verdict = f'{mkt_desc} | 情绪平淡，横盘整理'
        suggestion = '不参与，等待方向明确'
        level = '⚪'
    elif change >= -5:
        verdict = f'{mkt_desc} | 情绪退潮，下降趋势'
        suggestion = '止损或空仓'
        level = '🟠'
    else:
        verdict = f'{mkt_desc} | 情绪冰点，恐慌杀跌'
        suggestion = '忍住，不接飞刀'
        level = '🔴'
    return {'trader': '陈小群', 'verdict': verdict, 'suggestion': suggestion, 'level': level}


def analyze_chaoguyangjia(data: dict, kline: list, vol_ratio: float, market: dict) -> dict:
    """炒股养家：大局观心法（大盘氛围优先）"""
    change = data.get('change_pct', 0)
    high = data.get('high', 0)
    low = data.get('low', 0)
    price = data.get('price', 0)
    turnover = data.get('turnover', 0)
    total_mv = data.get('total_mv', 0)
    mkt = market.get('market_sentiment', '⚪')
    mkt_lvl = market.get('level', 0)
    mkt_avg = market.get('avg_change_pct', 0)

    today_range = round((high - low) / low * 100, 2) if low > 0 else 0
    ma5 = calc_ma(kline, 5) if kline else 0
    ma20 = calc_ma(kline, 20) if kline else 0

    # 大局观核心：大盘环境决定仓位
    if mkt_lvl >= 2:
        mkt_view = '大盘强势，大局观：积极持仓'
        base_level = 2
    elif mkt_lvl >= 1:
        mkt_view = '大盘平稳，大局观：控仓精选'
        base_level = 1
    elif mkt_lvl >= 0:
        mkt_view = '大盘偏弱，大局观：防守为主'
        base_level = 0
    else:
        mkt_view = f'大盘{mkt}，大局观：现金为王'
        base_level = -1

    if change >= 9.8:
        verdict = f'【大局观】{mkt_view} | 龙头地位确认，持有至尾盘'
        suggestion = '早盘炸板可出，缩量封板持有'
        level = '🟢'
    elif today_range > 10:
        verdict = f'【大局观】{mkt_view} | 振幅过大({today_range}%)，多空分歧'
        suggestion = '新手观望'
        level = '🟡'
    elif change >= 5:
        verdict = f'【大局观】{mkt_view} | 板块联动，赚钱效应扩散'
        suggestion = '顺势而为，不轻易下车'
        level = '🟢' if mkt_lvl >= 1 else '🟡'
    elif change >= 2:
        verdict = f'【大局观】{mkt_view} | 市值{total_mv}亿，流通{turnover}亿，等待催化'
        suggestion = f'MA5={ma5}，MA20={ma20}，方向确认再加仓'
        level = '🔵'
    elif change >= -2:
        verdict = f'【大局观】{mkt_view} | 横盘({today_range}%振幅)'
        suggestion = '控制仓位，不盲目加仓'
        level = '⚪'
    else:
        verdict = f'【大局观】{mkt_view} | 大盘弱，个股难独善其身'
        suggestion = '减仓或空仓'
        level = '🟠'
    return {'trader': '炒股养家', 'verdict': verdict, 'suggestion': suggestion, 'level': level}


def analyze_asking(data: dict, kline: list, vol_ratio: float, tiger: dict, market: dict) -> dict:
    """asking：龙头战法（大盘非必要条件，但有助力）"""
    change = data.get('change_pct', 0)
    amp = data.get('amp', 0)
    price = data.get('price', 0)
    bid1 = data.get('bid1_vol', 0)
    mkt = market.get('market_sentiment', '⚪')
    mkt_lvl = market.get('level', 0)

    ma5 = calc_ma(kline, 5) if kline else 0
    ma10 = calc_ma(kline, 10) if kline else 0
    has_tiger = bool(tiger.get('date'))
    net_buy = tiger.get('netBuy', 0) if tiger else 0

    if change >= 9.8:
        if has_tiger:
            verdict = f'涨停+龙虎(净买{net_buy}万)，asking：龙头战法核心标的'
        else:
            verdict = '涨停板，龙头战法核心标的'
        suggestion = '首封可打，二封加仓，炸板必出'
        level = '🟢'
    elif change >= 7:
        if mkt_lvl >= 1:
            verdict = f'冲击涨停，大盘{mkt}助力，龙头气质显现'
        else:
            verdict = '冲击涨停，大盘偏弱需谨慎'
        suggestion = '高位震荡若不破均价，可持有'
        level = '🟡'
    elif change >= 5:
        if mkt_lvl >= 1:
            verdict = f'强势股，asking：强者恒强，大盘助力'
        else:
            verdict = f'强势股，asking：强者恒强，但大盘平淡'
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


def analyze_chanlun(data: dict, kline: list, market: dict) -> dict:
    """缠中说禅：走势终完美（大盘提供环境参考）"""
    change = data.get('change_pct', 0)
    price = data.get('price', 0)
    mkt = market.get('market_sentiment', '⚪')
    mkt_avg = market.get('avg_change_pct', 0)

    if not kline or len(kline) < 20:
        return {'trader': '缠中说禅', 'verdict': f'数据不足，大盘{mkt}', 'suggestion': '等待K线数据', 'level': '⚪'}

    ma5 = calc_ma(kline, 5)
    ma10 = calc_ma(kline, 10)
    ma20 = calc_ma(kline, 20)
    ma30 = calc_ma(kline, 30) if len(kline) >= 30 else 0

    bull_alignment = ma5 > ma10 > ma20 if ma20 else ma5 > ma10
    bear_alignment = ma5 < ma10 < ma20 if ma20 else ma5 < ma10
    above_all = price > ma5 > ma10 > ma20 if ma20 else price > ma5 > ma10

    if change >= 9.8:
        verdict = '涨停，一买点确认后延续，走势终完美已实现'
        suggestion = '第二买点不破涨停板可入'
        level = '🟢'
    elif change >= 5:
        if above_all:
            verdict = f'上升走势延续，中枢上方运行，均线多头（大盘{mkt}）'
        else:
            verdict = f'上升走势，需观察能否重回中枢（大盘{mkt}）'
        suggestion = '持有或回调不破MA5加仓'
        level = '🟡'
    elif change >= 0:
        if bull_alignment:
            verdict = f'横盘震荡构建上涨中枢，均线多头：MA5={ma5} MA10={ma10}（大盘{mkt}）'
        else:
            verdict = f'横盘震荡，均线纠缠，耐心等待三买（大盘{mkt}）'
        suggestion = '不追，等次级别回调企稳'
        level = '🔵'
    else:
        if bear_alignment:
            verdict = f'下跌走势延续，缠论：下跌走势结束前不抄底（大盘{mkt}）'
            suggestion = '等待一买或二买信号'
        else:
            verdict = f'下跌走势，需观察是否形成底部中枢（大盘{mkt}）'
            suggestion = '等待震荡企稳'
        level = '🔴'
    return {'trader': '缠中说禅', 'verdict': verdict, 'suggestion': suggestion, 'level': level}


def analyze_hujialou(data: dict, kline: list, tiger: dict, market: dict) -> dict:
    """呼家楼：政策驱动 + 大盘共振为最强信号"""
    change = data.get('change_pct', 0)
    price = data.get('price', 0)
    total_mv = data.get('total_mv', 0)
    mkt = market.get('market_sentiment', '⚪')
    mkt_lvl = market.get('level', 0)
    mkt_avg = market.get('avg_change_pct', 0)
    is_large_cap = total_mv > 100
    has_tiger = bool(tiger.get('date'))

    # 大盘共振 = 呼家楼最强信号
    market_resonance = mkt_lvl >= 2 and change > 0
    market_headwind = mkt_lvl <= -1 and change < 0

    if change >= 9.8:
        base = f'{"大盘共振+" if market_resonance else ""}政策催化+龙头确认，{"大盘" if is_large_cap else "小盘"}股'
        verdict = f'【呼家楼】{base}，重仓出击'
        suggestion = '核心龙头满仓，止损+8%'
        level = '🟢'
    elif change >= 7:
        if market_resonance:
            verdict = f'【呼家楼】大盘共振·政策驱动，{"大盘" if is_large_cap else "小盘"}股，顺势重仓'
        else:
            verdict = f'【呼家楼】政策驱动，{"大盘" if is_large_cap else "小盘"}股，轻仓试探'
        suggestion = '回调站稳均线可加仓'
        level = '🟡'
    elif change >= 5:
        if mkt_lvl >= 1:
            verdict = f'【呼家楼】大盘{mkt}，板块轮动机会，轻仓参与'
        else:
            verdict = f'【呼家楼】大盘平淡，谨慎参与'
        suggestion = '不超过三成仓'
        level = '🔵'
    elif change >= 0:
        if market_resonance:
            verdict = f'【呼家楼】大盘共振信号出现，密切关注'
        else:
            verdict = f'【呼家楼】无明显催化，大盘{mkt}'
        suggestion = '不操作，等风来'
        level = '⚪'
    else:
        if market_headwind:
            verdict = f'【呼家楼】大盘拖累·政策逆风，{"大盘" if is_large_cap else "小盘"}股，不参与'
        else:
            verdict = f'【呼家楼】逆政策方向，政策底未明'
        suggestion = '空仓'
        level = '🔴'
    return {'trader': '呼家楼', 'verdict': verdict, 'suggestion': suggestion, 'level': level}


# ── 综合分析入口 ─────────────────────────────────────────────────────────

def full_analysis(stock_code: str, include_kline: bool = False) -> dict:
    """综合分析入口"""
    data = get_realtime(stock_code)
    if not data:
        return {'error': '无法获取行情数据，请检查股票代码'}
    if data.get('price', 0) == 0:
        return {'error': '股票代码无效'}

    zt = get_zt_status(data)
    data.update(zt)

    # 大盘数据
    market = get_market_sentiment()

    kline = []
    vol_ratio = 1.0
    tiger = {}

    if include_kline:
        kline = get_kline_pytdx(stock_code, days=60)
        vol_ratio = calc_vol_ratio(kline) if kline else 1.0
        tiger = get_dragon_tiger(stock_code)

    # 技术指标
    ma5 = calc_ma(kline, 5) if kline else 0
    ma10 = calc_ma(kline, 10) if kline else 0
    ma20 = calc_ma(kline, 20) if kline else 0

    # 五框架分析（传入大盘）
    analysts = [
        analyze_chenxiaoqun(data, kline, vol_ratio, market),
        analyze_chaoguyangjia(data, kline, vol_ratio, market),
        analyze_asking(data, kline, vol_ratio, tiger, market),
        analyze_chanlun(data, kline, market),
        analyze_hujialou(data, kline, tiger, market),
    ]

    # 打分（加入大盘修正）
    scores_map = {'🟢': 5, '🟡': 3, '🔵': 2, '⚪': 1, '🟠': 0, '🔴': -1}
    mkt_bonus = market.get('level', 0)  # 大盘正向额外加分
    total = sum(scores_map.get(a['level'], 0) for a in analysts) + mkt_bonus
    bullish = sum(1 for a in analysts if a['level'] in ('🟢', '🟡'))
    bearish = sum(1 for a in analysts if a['level'] in ('🟠', '🔴'))

    if total >= 22:
        overall = '🥇 强烈推荐买入'
        action = '大盘助力，建议关注，适时介入'
    elif total >= 14:
        overall = '🟡 谨慎关注'
        action = '轻仓试探，严格止损'
    elif total >= 7:
        overall = '⚪ 观望'
        action = '等待方向明确'
    else:
        overall = '🔴 建议回避'
        action = '控制风险，不参与'

    result = {
        'data': data,
        'market': {
            'sentiment': market.get('sentiment', '⚪'),
            'level': market.get('level', 0),
            'avg_change_pct': market.get('avg_change_pct', 0),
            'indices': market.get('indices', {}),
        },
        'analysts': analysts,
        'overall': overall,
        'action': action,
        'score': total,
        'score_max': 27,  # 25 + 大盘±2
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


@app.route('/api/market')
def api_market():
    """大盘数据接口"""
    market = get_market_sentiment()
    return jsonify(market)


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


@app.route('/api/tiger')
def api_tiger():
    code = request.args.get('code', '')
    if not code:
        return jsonify({'error': '缺少code参数'})
    return jsonify(get_dragon_tiger(code))


@app.route('/api/kline')
def api_kline():
    code = request.args.get('code', '')
    if not code:
        return jsonify({'error': '缺少code参数'})
    return jsonify({'code': code, 'kline': get_kline_pytdx(code)})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=7860, debug=False)
