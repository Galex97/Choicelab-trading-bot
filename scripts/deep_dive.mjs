import https from 'node:https';

function httpGet(url) {
  return new Promise((resolve) => {
    https.get(url, { headers: { 'User-Agent': 'Mozilla/5.0' } }, (res) => {
      let d = '';
      res.on('data', (c) => { d += c; });
      res.on('end', () => {
        try { resolve(JSON.parse(d)); } catch (e) { resolve(null); }
      });
    }).on('error', () => resolve(null));
  });
}

async function getDailyKline(symbol, days) {
  const url = 'https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData' +
    '?symbol=' + symbol + '&scale=240&ma=no&datalen=' + days;
  const res = await httpGet(url);
  if (Array.isArray(res)) {
    return res.map(d => ({
      date: d.day.substring(0, 10),
      open: parseFloat(d.open),
      close: parseFloat(d.close),
      high: parseFloat(d.high),
      low: parseFloat(d.low),
      volume: parseFloat(d.volume)
    }));
  }
  return null;
}

const picks = [
  { code: '000429', name: '粤高速A', sym: 'sz000429', pe: 15, roe: 16.2 },
  { code: '000333', name: '美的集团', sym: 'sz000333', pe: 13, roe: 19.7 },
  { code: '000600', name: '建投能源', sym: 'sz000600', pe: 11, roe: 12.3 },
  { code: '000612', name: '焦作万方', sym: 'sz000612', pe: 13, roe: 14.9 },
  { code: '000543', name: '皖能电力', sym: 'sz000543', pe: 11, roe: 11.1 },
];

async function analyze(name, sym, pe, roe) {
  console.log('\n' + '='.repeat(80));
  console.log(name + '  PE=' + pe + '  ROE=' + roe + '%');
  console.log('='.repeat(80));

  const kl = await getDailyKline(sym, 30);
  if (!kl) { console.log('No data'); return; }

  // Show last 15 days
  console.log('日期        开盘     收盘     最高     最低      成交量(万)   日涨跌%   量比');
  console.log('-'.repeat(75));

  for (let i = Math.max(0, kl.length - 15); i < kl.length; i++) {
    const d = kl[i];
    const prev = kl[i - 1];
    const chg = prev ? ((d.close - prev.close) / prev.close * 100).toFixed(2) : '-';
    const prevVol = prev ? prev.volume : d.volume;
    const vRatio = (d.volume / prevVol).toFixed(2);

    // Color-coded indicators
    let flag = '';
    if (chg !== '-' && parseFloat(chg) > 5) flag = ' << 大阳';
    else if (chg !== '-' && parseFloat(chg) > 9.5) flag = ' << 涨停';
    else if (chg !== '-' && parseFloat(chg) < -5) flag = ' << 大阴';
    else if (parseFloat(vRatio) > 2) flag = ' << 放量';
    else if (parseFloat(vRatio) < 0.5) flag = ' << 缩量';

    console.log(
      d.date + '  ' +
      String(d.open).padStart(7) + '  ' +
      String(d.close).padStart(7) + '  ' +
      String(d.high).padStart(7) + '  ' +
      String(d.low).padStart(7) + '  ' +
      String(Math.round(d.volume / 10000)).padStart(12) + '  ' +
      (chg === '-' ? '   -' : String(chg).padStart(6) + '%') + '  ' +
      vRatio +
      flag
    );
  }

  // Key statistics
  const prices = kl.map(k => k.close);
  const vols = kl.map(k => k.volume);
  const recent10 = kl.slice(-10);
  const recent5 = kl.slice(-5);

  // Up/down day ratio in last 10
  let upDays = 0, downDays = 0;
  for (let i = 1; i < recent10.length; i++) {
    if (recent10[i].close > recent10[i - 1].close) upDays++;
    else downDays++;
  }
  const upRatio = recent10.length > 1 ? (upDays / (upDays + downDays) * 100).toFixed(0) : '-';

  // Consecutive up/down
  let consecUp = 0, consecDown = 0;
  for (let i = kl.length - 1; i > 0; i--) {
    if (kl[i].close > kl[i - 1].close) consecUp++;
    else break;
  }
  for (let i = kl.length - 1; i > 0; i--) {
    if (kl[i].close < kl[i - 1].close) consecDown++;
    else break;
  }

  // Support/resistance
  const high20 = Math.max(...recent10.map(k => k.high));
  const low20 = Math.min(...recent10.map(k => k.low));
  const range = high20 - low20;
  const currentPos = ((kl[kl.length - 1].close - low20) / range * 100).toFixed(0);

  // Volume trend
  const avgVol10 = vols.slice(-10).reduce((s, v) => s + v, 0) / 10;
  const avgVol20 = vols.slice(-20).reduce((s, v) => s + v, 0) / 20;
  const volTrend = ((avgVol10 / avgVol20 - 1) * 100).toFixed(0);

  console.log('\n--- 技术面诊断 ---');
  console.log('近10日: ' + upDays + '涨 ' + downDays + '跌  胜率: ' + upRatio + '%');
  console.log('连续: ' + (consecUp > 0 ? consecUp + '日上涨' : consecDown > 0 ? consecDown + '日下跌' : '横盘'));
  console.log('10日区间: ' + low20.toFixed(2) + ' - ' + high20.toFixed(2) + '  当前位置: ' + currentPos + '% (0%=底部 100%=顶部)');
  console.log('量能趋势: ' + (parseFloat(volTrend) > 0 ? '+' : '') + volTrend + '% (近10日 vs 近20日)');
  console.log('最近5日涨跌: ' + recent5.map((k, i) => {
    const prev = recent5[i - 1] || recent5[0];
    return (i === 0 ? '首日' : ((k.close - prev.close) / prev.close * 100).toFixed(1) + '%');
  }).join(' → '));
}

async function main() {
  for (const p of picks) {
    await analyze(p.name, p.sym, p.pe, p.roe);
    await new Promise(r => setTimeout(r, 300));
  }

  // Also: check the user's holdings
  console.log('\n\n' + '='.repeat(80));
  console.log('持仓对比：002407 多氟多 & 603986 兆易创新');
  console.log('='.repeat(80));

  const holdings = [
    { name: '002407 多氟多', sym: 'sz002407' },
    { name: '603986 兆易创新', sym: 'sh603986' }
  ];
  for (const h of holdings) {
    const kl = await getDailyKline(h.sym, 30);
    if (!kl) continue;
    const prices = kl.map(k => k.close);
    const recent5 = kl.slice(-5);
    const ret5 = ((prices[prices.length - 1] - prices[prices.length - 6]) / prices[prices.length - 6] * 100).toFixed(1);
    const ret20 = ((prices[prices.length - 1] - prices[0]) / prices[0] * 100).toFixed(1);

    // RSI-like: count up days in last 14
    let gains = 0, losses = 0;
    for (let i = kl.length - 14; i < kl.length; i++) {
      const chg = kl[i].close - kl[i - 1].close;
      if (chg > 0) gains += chg;
      else losses += Math.abs(chg);
    }
    const rsi14 = gains + losses > 0 ? (gains / (gains + losses) * 100).toFixed(0) : 50;

    console.log('\n' + h.name + ': 20日=' + ret20 + '%  5日=' + ret5 + '%  RSI14=' + rsi14 +
      '  ' + (parseInt(rsi14) > 70 ? '【超买】' : parseInt(rsi14) < 30 ? '【超卖】' : '【中性】'));
  }

  console.log('\n\n分析完成。');
}

main().catch(e => console.error(e));
