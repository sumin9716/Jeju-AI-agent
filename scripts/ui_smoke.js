// UI 스모크 테스트 (Playwright). API 키 없이 녹화된 응답으로 화면 전체 흐름을 점검한다.
//   1) JEJU_AGENT_DEMO_SCRIPT=tests/fixtures/demo_script.json streamlit run app.py --server.headless true
//   2) node scripts/ui_smoke.js            (스크린샷: ui_initial.png, ui_after.png)
const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1400, height: 1000 }, deviceScaleFactor: 1 });
  const errors = [];
  page.on('pageerror', e => errors.push(String(e)));
  await page.goto('http://localhost:8501/', { waitUntil: 'networkidle' });
  await page.waitForSelector('text=제주 데이터 길잡이', { timeout: 60000 });
  await page.screenshot({ path: 'ui_initial.png', fullPage: true });
  const btn = page.locator('button', { hasText: '애월읍은 주말과 평일' }).first();
  await btn.click();
  try {
    await page.waitForSelector('text=분석 과정 보기', { timeout: 90000 });
  } catch (e) {
    console.log('wait failed, dumping body');
  }
  await page.waitForTimeout(4000);
  await page.screenshot({ path: 'ui_after.png', fullPage: true });
  const body = await page.locator('body').innerText();
  console.log('has traceback:', /Traceback/.test(body));
  console.log('plotly count:', await page.locator('.js-plotly-plot').count());
  console.log('has download button:', body.includes('보고서 HTML'));
  console.log('page errors:', errors.length, errors.slice(0,3));
  console.log('---- body (first 80 lines) ----');
  console.log(body.split('\n').filter(l => l.trim()).slice(0, 80).join('\n'));
  await browser.close();
})().catch(e => { console.error('TEST FAILED', e); process.exit(1); });
