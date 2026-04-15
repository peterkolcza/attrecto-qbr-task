const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });

  // Landing page
  await page.goto('http://127.0.0.1:8888/', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(1000);
  await page.screenshot({ path: '/tmp/qbr_final_1_landing.png', fullPage: true });
  console.log('1. Landing page screenshot saved');

  // Job page (completed)
  await page.goto('http://127.0.0.1:8888/jobs/4aeaf6d7', { waitUntil: 'domcontentloaded', timeout: 10000 });
  await page.waitForTimeout(1000);
  await page.screenshot({ path: '/tmp/qbr_final_2_job.png', fullPage: true });
  console.log('2. Job page screenshot saved');

  // Report page
  const reportLink = await page.$('a:has-text("View Full Report")');
  if (reportLink) {
    await reportLink.click();
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(1000);
    await page.screenshot({ path: '/tmp/qbr_final_3_report.png', fullPage: true });
    console.log('3. Report page screenshot saved');
  } else {
    console.log('3. No report link found');
  }

  await browser.close();
})();
