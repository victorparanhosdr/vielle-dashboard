const assert = require('node:assert/strict');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
(async () => {
  const browser = await chromium.launch({headless: true, executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto('http://127.0.0.1:8778/?clinic=vielle');
    await page.locator('#login').fill('excel');
    await page.locator('#password').fill('ExcelLocal2026!');
    await page.locator('#loginSubmit').click();
    await page.locator('.chartExportButton:not(:disabled)').first().waitFor();
    assert.equal(await page.locator('.chartExportButton:visible').count(), 8);
    for (const width of [1440, 390, 320]) {
      await page.setViewportSize({width, height: 900});
      for (const button of await page.locator('.chartExportButton').all()) {
        await button.scrollIntoViewIfNeeded();
        const bounds = await button.boundingBox();
        assert(bounds.x >= 0 && bounds.x + bounds.width <= width, `Export outside viewport ${width}`);
        assert(await button.locator('img').evaluate(img => img.complete && img.naturalWidth > 0));
      }
      await page.locator('#generalRevenueBarChart').scrollIntoViewIfNeeded();
      await page.screenshot({path: `/tmp/doc4docs-excel-${width}.png`});
    }
    for (const button of await page.locator('.chartExportButton').all()) {
      const promise = page.waitForEvent('download');
      await button.click();
      const download = await promise;
      assert(download.suggestedFilename().endsWith('.xlsx'));
      assert.equal(await download.failure(), null);
    }
    assert.deepEqual(errors, []);
    console.log('PASS: 8 exports, desktop/mobile/320px, images, downloads, no JS errors');
  } finally {
    await browser.close();
  }
})().catch(error => {console.error(error); process.exit(1);});
