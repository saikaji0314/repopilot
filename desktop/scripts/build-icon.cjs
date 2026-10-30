// Export the existing licensed RepoPilot repository mark at Windows icon sizes.
const fs = require('node:fs')
const path = require('node:path')
const { chromium } = require('@playwright/test')
async function main() {
  const directory = path.resolve(__dirname, '../assets')
  const mark = fs.readFileSync(path.resolve(__dirname, '../src/assets/imgOcticonRepo1.svg'), 'utf8').replace(/<svg[^>]*>/, '').replace('</svg>', '').replace(/#1F2328/gi, '#ffffff')
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256"><rect width="256" height="256" rx="48" fill="#0969da"/><g transform="translate(48 48) scale(8)">${mark}</g></svg>`
  fs.writeFileSync(path.join(directory, 'icon.svg'), svg)
  const browser = await chromium.launch({ executablePath: process.env.REPOPILOT_BROWSER || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe' })
  try {
    const sizes = [16, 24, 32, 48, 64, 128, 256]; const images = []
    const page = await browser.newPage()
    for (const size of sizes) {
      await page.setViewportSize({ width: size, height: size })
      await page.setContent(`<style>html,body{margin:0;background:transparent}svg{display:block;width:100%;height:100%}</style>${svg}`)
      images.push(await page.screenshot({ omitBackground: true }))
    }
    const header = Buffer.alloc(6 + sizes.length * 16); header.writeUInt16LE(1, 2); header.writeUInt16LE(sizes.length, 4)
    let offset = header.length
    sizes.forEach((size, i) => { const at = 6 + i * 16; header[at] = size % 256; header[at + 1] = size % 256; header.writeUInt16LE(1, at + 4); header.writeUInt16LE(32, at + 6); header.writeUInt32LE(images[i].length, at + 8); header.writeUInt32LE(offset, at + 12); offset += images[i].length })
    fs.writeFileSync(path.join(directory, 'icon.ico'), Buffer.concat([header, ...images]))
    fs.writeFileSync(path.join(directory, 'icon.png'), images.at(-1))
  } finally { await browser.close() }
}
main().catch(error => { console.error(error); process.exitCode = 1 })
