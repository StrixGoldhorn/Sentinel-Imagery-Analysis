"""Copernicus evalscripts used by imagery adapters."""

SAR = """//VERSION=3
function setup() {
  return {
    input: ["VH", "dataMask"],
    output: {bands: 4, sampleType: SampleType.UINT8}
  };
}
function evaluatePixel(sample) {
  let alpha = sample.dataMask === 1 ? 255 : 0;
  if (alpha === 0) return [0, 0, 0, 0];
  let grayValue = Math.min(255, Math.round(sample.VH * 2 * 255));
  return [grayValue, grayValue, grayValue, alpha];
}"""

DEM = """//VERSION=3
function setup() {
  return {
    input: ["DEM", "dataMask"],
    output: {bands: 4, sampleType: SampleType.UINT8}
  };
}
function evaluatePixel(sample) {
  let alpha = sample.dataMask === 1 ? 255 : 0;
  if (alpha === 0) return [0, 0, 0, 0];
  let shifted = Math.max(0, sample.DEM + 50);
  let grayValue = Math.round(Math.sqrt(shifted / 200) * 255);
  return [grayValue, grayValue, grayValue, alpha];
}"""

