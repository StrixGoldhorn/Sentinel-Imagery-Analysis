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
    output: {bands: 4, sampleType: "UINT8"}
  };
}
function evaluatePixel(sample) {
  if (sample.dataMask === 0 || sample.DEM <= 0) {
    return [0, 0, 0, 255];
  }
  let grayValue = Math.min(255, Math.max(20, Math.round(Math.sqrt(sample.DEM) * 16)));
  return [grayValue, grayValue, grayValue, 255];
}"""

SAR_DUAL_POL = """//VERSION=3
function setup() {
  return {
    input: ["VV", "VH", "dataMask"],
    output: {bands: 4, sampleType: SampleType.UINT8}
  };
}
function evaluatePixel(sample) {
  let alpha = sample.dataMask === 1 ? 255 : 0;
  if (alpha === 0) return [0, 0, 0, 0];
  let vvVal = Math.min(255, Math.round(sample.VV * 2 * 255));
  let vhVal = Math.min(255, Math.round(sample.VH * 2 * 255));
  let ratio = Math.min(255, Math.round((sample.VH / (sample.VV + 0.001)) * 64));
  return [vvVal, vhVal, ratio, alpha];
}"""


