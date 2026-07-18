import fs from 'node:fs';
import http from 'node:http';
import https from 'node:https';

function getJson(url) {
  return new Promise((resolve) => {
    const mod = url.startsWith('https') ? https : http;
    mod.get(url, { headers: { 'User-Agent': 'Mozilla/5.0' } }, (res) => {
      let body = '';
      res.on('data', (chunk) => { body += chunk; });
      res.on('end', () => {
        try { resolve(JSON.parse(body)); } catch { resolve(null); }
      });
    }).on('error', () => resolve(null));
  });
}

function parseCsv(text) {
  const lines = text.trim().split(/\r?\n/);
  const headers = lines[0].split(',').map((h) => h.replace(/^"|"$/g, ''));
  return lines.slice(1).map((line) => {
    const vals = line.split(',').map((v) => v.replace(/^"|"$/g, ''));
    const row = {};
    headers.forEach((h, i) => { row[h] = vals[i] ?? ''; });
    return row;
  });
}

function marketPrefix(code) {
  return code.startsWith('6') ? '1' : '0';
}

async function getQuotes(codes) {
  const quotes = {};
  for (let i = 0; i < codes.length; i += 50) {
    const secids = codes.slice(i, i + 50).map((code) => `${marketPrefix(code)}.${code}`).join(',');
    const url = 'http://push2.eastmoney.com/api/qt/ulist.np/get' +
      '?fltt=2&invt=2&fields=f2,f3,f4,f9,f12,f14,f20,f21,f23,f100' +
      `&secids=${secids}`;
    const data = await getJson(url);
    for (const item of data?.data?.diff ?? []) {
      quotes[item.f12] = {
        code: item.f12,
        name: item.f14,
        price: item.f2,
        changePct: item.f3,
        pe: item.f9,
        pb: item.f23,
        mktCap: item.f20 ? item.f20 / 100000000 : null,
        industryName: item.f100 || '',
      };
    }
    await new Promise((resolve) => setTimeout(resolve, 150));
  }
  return quotes;
}

async function getKline(code, days = 40) {
  const symbol = `${code.startsWith('6') ? 'sh' : 'sz'}${code}`;
  const url = 'https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/' +
    `CN_MarketData.getKLineData?symbol=${symbol}&scale=240&ma=no&datalen=${days}`;
  const data = await getJson(url);
  if (!Array.isArray(data)) return null;
  return data.map((d) => ({
    date: String(d.day).slice(0, 10),
    open: Number(d.open),
    close: Number(d.close),
    high: Number(d.high),
    low: Number(d.low),
    volume: Number(d.volume),
  })).filter((d) => Number.isFinite(d.close) && d.close > 0);
}

function metrics(klines) {
  if (!klines || klines.length < 21) return null;
  const closes = klines.map((k) => k.close);
  const last = klines.at(-1);
  const prev = klines.at(-2);
  const ret = (days) => (last.close - closes.at(-(days + 1))) / closes.at(-(days + 1)) * 100;
  const last20 = klines.slice(-20);
  const last10 = klines.slice(-10);

  let peak = last20[0].close;
  let maxDD = 0;
  for (const k of last20) {
    if (k.close > peak) peak = k.close;
    maxDD = Math.max(maxDD, (peak - k.close) / peak * 100);
  }

  const rangeLow = Math.min(...last10.map((k) => k.low));
  const rangeHigh = Math.max(...last10.map((k) => k.high));
  const rangePos = rangeHigh === rangeLow ? 50 : (last.close - rangeLow) / (rangeHigh - rangeLow) * 100;
  const avgAmp = last20.reduce((sum, k, idx) => {
    const base = idx === 0 ? k.open : last20[idx - 1].close;
    return sum + (k.high - k.low) / base * 100;
  }, 0) / last20.length;
  const avgVol20 = last20.reduce((sum, k) => sum + k.volume, 0) / last20.length;
  const avgVol5 = klines.slice(-5).reduce((sum, k) => sum + k.volume, 0) / 5;
  const lastDayPct = (last.close - prev.close) / prev.close * 100;
  let recentBigDown = false;
  let bigReversal = false;
  let swingDays = 0;
  for (let i = Math.max(1, klines.length - 8); i < klines.length; i += 1) {
    const pct = (klines[i].close - klines[i - 1].close) / klines[i - 1].close * 100;
    const prevPct = i >= 2 ? (klines[i - 1].close - klines[i - 2].close) / klines[i - 2].close * 100 : 0;
    if (pct < -5) recentBigDown = true;
    if (pct > 5 || pct < -5) swingDays += 1;
    if (prevPct > 8 && pct < -5) bigReversal = true;
  }

  let gains = 0;
  let losses = 0;
  for (let i = klines.length - 14; i < klines.length; i += 1) {
    const chg = klines[i].close - klines[i - 1].close;
    if (chg >= 0) gains += chg;
    else losses -= chg;
  }
  const rsi14 = gains + losses === 0 ? 50 : gains / (gains + losses) * 100;

  return {
    latestDate: last.date,
    ret5: ret(5),
    ret10: ret(10),
    ret20: ret(20),
    maxDD,
    avgAmp,
    rangePos,
    volRatio: avgVol20 > 0 ? avgVol5 / avgVol20 : 1,
    lastDayPct,
    recentBigDown,
    bigReversal,
    swingDays,
    rsi14,
  };
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function score(stock, quote, m) {
  const pe = quote?.pe > 0 ? quote.pe : stock.pe;
  const pb = quote?.pb > 0 ? quote.pb : stock.pb;
  const cap = quote?.mktCap ?? stock.mktCap;

  const quality = clamp(stock.roe / 20, 0, 1) * 24 + clamp(stock.eps / 2, 0, 1) * 6;
  const valuation = clamp(15 / pe, 0, 1) * 16 + clamp(2 / pb, 0, 1) * 8;
  const entry = (1 - clamp(Math.abs(m.rangePos - 35) / 65, 0, 1)) * 16 +
    (1 - clamp(m.avgAmp / 6, 0, 1)) * 6;
  const momentum = (m.ret20 >= -3 && m.ret20 <= 15 ? 13 : 0) +
    (m.ret5 >= -4 && m.ret5 <= 6 ? 5 : 0);
  const liquidity = cap >= 100 ? 5 : cap >= 50 ? 3 : 1;

  let penalty = 0;
  if (m.rangePos > 85) penalty += 6;
  if (m.rsi14 > 70) penalty += 5;
  if (m.ret20 > 25) penalty += 8;
  if (m.maxDD > 12) penalty += 5;
  if (m.lastDayPct > 6) penalty += 4;
  if (m.recentBigDown) penalty += 6;
  if (m.bigReversal) penalty += 12;
  if (m.swingDays >= 2) penalty += 5;

  return quality + valuation + entry + momentum + liquidity - penalty;
}

const csvPath = process.env.FUNDAMENTALS_CSV;
if (!csvPath || !fs.existsSync(csvPath)) {
  throw new Error('Set FUNDAMENTALS_CSV to a locally licensed fundamentals CSV. No market-data file is bundled with this repository.');
}
const csv = fs.readFileSync(csvPath, 'utf8');
const rows = parseCsv(csv);
const byCode = new Map();
for (const row of rows) {
  if (!byCode.has(row.Stkcd)) byCode.set(row.Stkcd, []);
  byCode.get(row.Stkcd).push(row);
}

const fundamentals = [];
for (const [code, stockRows] of byCode.entries()) {
  stockRows.sort((a, b) => b.Date.localeCompare(a.Date));
  const latest = stockRows[0];
  const pe = Number(latest.PE);
  const pb = Number(latest.PB);
  const roe = Number(latest.ROE);
  const eps = Number(latest.EPS);
  const price = Number(latest.Clpr);
  const shares = Number(latest.Fullshr);
  if (latest.Listedstate !== 'Norm') continue;
  if (!(pe > 0 && pe < 80 && roe > 5 && eps > 0 && pb > 0)) continue;
  fundamentals.push({
    code,
    name: latest.Lstknm,
    industry: latest.Csrciccd1,
    pe,
    pb,
    roe,
    eps,
    mktCap: price * shares / 100000000,
  });
}

const quotes = await getQuotes(fundamentals.map((f) => f.code));
const ranked = [];
for (const stock of fundamentals) {
  const klines = await getKline(stock.code);
  const m = metrics(klines);
  if (!m) continue;
  ranked.push({
    ...stock,
    quote: quotes[stock.code],
    metrics: m,
    agentBScore: score(stock, quotes[stock.code], m),
  });
  await new Promise((resolve) => setTimeout(resolve, 120));
}

ranked.sort((a, b) => b.agentBScore - a.agentBScore);
console.log(JSON.stringify(ranked.slice(0, 15).map((s, idx) => ({
  rank: idx + 1,
  code: s.code,
  name: s.name,
  price: s.quote?.price ?? null,
  pe: Number((s.quote?.pe || s.pe).toFixed(2)),
  pb: Number((s.quote?.pb || s.pb).toFixed(2)),
  roe: Number(s.roe.toFixed(2)),
  mktCap: Number((s.quote?.mktCap || s.mktCap).toFixed(0)),
  ret5: Number(s.metrics.ret5.toFixed(1)),
  ret20: Number(s.metrics.ret20.toFixed(1)),
  maxDD: Number(s.metrics.maxDD.toFixed(1)),
  rangePos: Number(s.metrics.rangePos.toFixed(0)),
  avgAmp: Number(s.metrics.avgAmp.toFixed(1)),
  rsi14: Number(s.metrics.rsi14.toFixed(0)),
  bigReversal: s.metrics.bigReversal,
  recentBigDown: s.metrics.recentBigDown,
  score: Number(s.agentBScore.toFixed(1)),
  latestDate: s.metrics.latestDate,
})), null, 2));
