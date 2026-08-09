const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const css = fs.readFileSync(path.join(__dirname, 'theme.css'), 'utf8');

const themeVariables = theme => {
  const selector = theme === 'morning' ? ':root' : `html[data-theme="${theme}"]`;
  const start = css.indexOf(selector);
  const open = css.indexOf('{', start);
  const close = css.indexOf('}', open);
  assert.notEqual(start, -1, `${selector} must exist`);
  return Object.fromEntries(
    [...css.slice(open + 1, close).matchAll(/--([\w-]+):\s*(#[0-9a-f]{6})\s*;/gi)]
      .map(match => [match[1], match[2]])
  );
};

const rgb = hex => [1, 3, 5].map(index => Number.parseInt(hex.slice(index, index + 2), 16));
const luminance = hex => rgb(hex)
  .map(value => {
    const channel = value / 255;
    return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
  })
  .reduce((sum, value, index) => sum + value * [0.2126, 0.7152, 0.0722][index], 0);
const contrast = (left, right) => {
  const values = [luminance(left), luminance(right)].sort((a, b) => b - a);
  return (values[0] + 0.05) / (values[1] + 0.05);
};
const mix = (foreground, percentage, background) => {
  const foregroundRgb = rgb(foreground);
  const backgroundRgb = rgb(background);
  return `#${foregroundRgb.map((value, index) => (
    Math.round(value * percentage + backgroundRgb[index] * (1 - percentage))
      .toString(16)
      .padStart(2, '0')
  )).join('')}`;
};

for (const theme of ['morning', 'midday', 'afternoon', 'evening']) {
  test(`${theme} palette keeps text and focus indicators readable`, () => {
    const variables = themeVariables(theme);
    assert.ok(contrast(variables['on-accent'], variables.violet) >= 4.5, 'accent text must meet AA');
    assert.ok(contrast(variables.muted, variables.paper) >= 4.5, 'muted text must meet AA on paper');
    assert.ok(contrast(variables.muted, variables['paper-soft']) >= 4.5, 'muted text must meet AA on soft paper');
    assert.ok(contrast(variables['sidebar-muted'], variables.sidebar) >= 4.5, 'muted sidebar text must meet AA');
    assert.ok(contrast(variables.ink, variables['paper-muted']) >= 4.5, 'number badges must meet AA');
    assert.ok(contrast(variables['focus-ring'], variables.paper) >= 3, 'focus ring must contrast with paper');
    assert.ok(contrast(variables['focus-ring'], variables['control-bg']) >= 3, 'focus ring must contrast with controls');

    for (const [token, percentage] of [['green', 0.15], ['red', 0.14], ['amber', 0.16], ['blue', 0.17]]) {
      const tintedBackground = mix(variables[token], percentage, variables.paper);
      assert.ok(
        contrast(variables[token], tintedBackground) >= 4.5,
        `${token} text must meet AA on its status tint`
      );
    }
  });
}
