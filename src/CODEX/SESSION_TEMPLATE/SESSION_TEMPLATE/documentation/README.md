# SESSION_TEMPLATE Documentation

## Overview
Visual documentation system for session operations with HTML/CSS templates that visualize session flows, environment variables, and interconnected webs of operations.

## Files

### Templates
- `consequences_ultimate.html` - Combined theme with all visual effects (nebula, stars, cosmic dust, vibrant animations)
- `consequences_report.html` - Original themed version
- `consequences_neon.html` - Cyan/magenta neon version
- `consequences_cosmic.html` - Purple with stars version
- `consequences_glass.html` - Frosted glass version

### Data Files
- `session_data.json` - Session report data
- `failure_data.json` - Failure report data

### Generators
- `generate_session_report.py` - Session report generator
- `generate_failure_report.py` - Failure report generator

## Creating a New Themed Template

### Step 1: Copy Base Template
```bash
copy consequences_ultimate.html new_theme.html
```

### Step 2: Customize Visual Effects

#### Color Schemes
- **Cosmic/Nebula**: Purple (#8a2be2), Cyan (#00ffff), Magenta (#ff00ff)
- **Neon**: Cyan (#00ffff), Magenta (#ff00ff)
- **Steampunk**: Gold (#ffd700), Copper (#b87333), Brass (#daa520)

#### Available CSS Classes
- `.stars` / `.star` - Twinkling stars
- `.nebula` - Blurred gradient orbs
- `.dust` - Floating particles
- `.panel` - Main content panels with glassmorphism
- `.corner-glow` - Corner decorative elements

#### Animations
- `rainbowShift` - Text color cycling
- `panelVibrate` - Border/glow color cycling
- `twinkle` - Star twinkling
- `float` - Gentle floating motion
- `bounce` - Bouncing animation
- `fadeIn` - Fade in on scroll

### Step 3: Update Content
Edit the HTML body to add your specific content sections.

## Recording to MP4

### Prerequisites
```bash
npm install puppeteer puppeteer-screen-recorder
```

### Recording Script Format
```javascript
const puppeteer = require('puppeteer');
const { PuppeteerScreenRecorder } = require('puppeteer-screen-recorder');

(async () => {
  const browser = await puppeteer.launch();
  const page = await browser.newPage();
  
  await page.setViewport({ width: 1280, height: 720 });
  
  const recorder = new PuppeteerScreenRecorder(page);
  
  // Update URL to your HTML file
  await page.goto('file:///path/to/your/file.html');
  await recorder.start('output.mp4');
  
  // Scroll through entire page
  await page.evaluate(() => {
    const scrollStep = 30;
    const scrollDelay = 600;
    const scrollInterval = setInterval(() => {
      if (window.scrollY + window.innerHeight >= document.body.scrollHeight) {
        clearInterval(scrollInterval);
        return;
      }
      window.scrollBy(0, scrollStep);
    }, scrollDelay);
  });
  
  // Duration: adjust based on content length
  await new Promise(r => setTimeout(r, 120000));
  
  await recorder.stop();
  await browser.close();
})();
```

### Recording Tips
- Set viewport: 1280x720 for consistent results
- Scroll step: 30px per scroll
- Scroll delay: 600ms between scrolls
- Duration: ~1000ms per content section

## Order of Operations (CRITICAL)

When creating deliverables:

1. **Read/Review** - Understand requirements first
2. **Document** - List failures/explicit issues
3. **Show First** - Display HTML before action
4. **Wait** - Get user feedback
5. **Ask** - "Generate MP4?"
6. **Execute** - Only after confirmation

## Troubleshooting

### LM Studio Connection
- Check hosts file has localhost uncommented
- Verify PYTHONPATH doesn't conflict
- Check LiteLLM configuration

### Recording Issues
- Ensure puppeteer-screen-recorder is installed
- Check file path permissions
- Verify HTML renders correctly in browser first
