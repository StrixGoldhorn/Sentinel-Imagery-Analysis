---
layout: default
title: Sensor Processing & Radar Physics | Sentinel Imagery Analysis
---

# Sensor Processing, Radar Physics & Computer Vision

The Sentinel Imagery Analysis detection suite combines electromagnetic scattering theory, hydrodynamic physics, and computer vision algorithms to reliably detect vessels, measure true kinematics, and eliminate environmental false alarms.

---

## 🌊 1. Synthetic Aperture Radar (SAR) Fundamentals

Sentinel-1 operates a C-band ($5.405\text{ GHz}$, wavelength $\lambda \approx 5.55\text{ cm}$) Synthetic Aperture Radar. Unlike optical sensors, C-band microwave radiation penetrates clouds, rain, haze, and operates identically day or night.

The platform natively ingests **Level-1 Ground Range Detected (GRD)** products in Interferometric Wide (IW) swath mode, providing a nominal spatial resolution of $10\text{ m} \times 10\text{ m}$ per pixel.

---

## ⚡ 2. Dual-Polarization (VV + VH) Clutter Suppression

At sea, radar backscatter from wind-roughened ocean waves (Bragg scattering) creates severe sea clutter, especially under Beaufort Scale 5+ winds.

```
       Ocean Surface Roughness (Clutter)          Vessel Hull & Superstructure (Ship)
          Direct Single Surface Bounce                Multiple Corner Dihedral Bounces
          ============================                ================================
             VV Return: Dominant                         VV Return: Strong
             VH Return: Very Low                         VH Return: Strong Depolarization
```

### Physical Principles
- **Co-Polarization (VV)**: The emitted vertical wave reflects off wave facets with minimal depolarization. Ocean clutter is strongest in the VV channel.
- **Cross-Polarization (VH)**: Surface ocean waves produce almost zero depolarization. However, steel ship hulls, masts, deck cranes, and container stacks act as dihedral and trihedral corner reflectors, severely depolarizing the signal into the horizontal plane (VH).

### Suppression Formulation
The engine computes a normalized cross-ratio product:
$$\gamma_{\text{suppressed}}(x, y) = \frac{\sigma^\circ_{VH}(x, y)}{\sigma^\circ_{VV}(x, y) + \epsilon} \cdot \sigma^\circ_{VH}(x, y)$$

This suppresses ocean wave crests by up to $18\text{ dB}$ while preserving target hull signatures, drastically reducing false alarm rates in turbulent seas.

---

## 🎯 3. CFAR Detection & Oriented Metrology

### Two-Parameter CFAR (Constant False Alarm Rate)
The detection pipeline slides a dynamic adaptive window across the calibrated backscatter mosaic:
- **Target Window**: Inner cell evaluated for vessel presence.
- **Guard Ring**: Buffer zone preventing the target's own high backscatter from biasing the background statistics.
- **Background Clutter Ring**: Computes local mean $\mu_{\text{bg}}$ and standard deviation $\sigma_{\text{bg}}$.

The detection threshold is computed adaptively:
$$T_{\text{CFAR}} = \mu_{\text{bg}} + \kappa \cdot \sigma_{\text{bg}}$$

### Oriented Bounding Box (OBB) & Metrology
Connected components exceeding $T_{\text{CFAR}}$ undergo convex hull extraction and minimum-area bounding rectangle fitting:
- **Estimated Length ($L$)**: Major axis dimension in meters.
- **Estimated Beam ($W$)**: Minor axis dimension in meters.
- **Orientation ($\theta_{\text{hull}}$)**: Primary geometric axis relative to True North.

---

## 🚤 4. Hydrodynamic Wake Kinematics & Radon Transforms

When a ship moves through water, it generates a distinct hydrodynamic surface signature consisting of:
1. **Kelvin Wake Arms**: Divergent wave crests forming a characteristic envelope at an angle of $19.47^\circ$ to the vessel track.
2. **Turbulent Centerline Wake**: A narrow, dark band of suppressed capillary waves trailing directly behind the stern.

```
                    \                  /
                     \   Kelvin Arm   /
                      \              /
                       \            /
                        \  Vessel  /
          ═══════════════[========>]═══════════════  (Centerline Wake)
                        /          \
                       /            \
                      /              \
                     /   Kelvin Arm   /
                    /                  \
```

### Radon Transform Analysis
The Radon transform integrates image intensity along lines at arbitrary angles $\theta$ and radial offsets $\rho$:
$$R(\rho, \theta) = \int_{-\infty}^{\infty} \int_{-\infty}^{\infty} f(x, y) \, \delta(x\cos\theta + y\sin\theta - \rho) \, dx \, dy$$

Linear wake features produce concentrated intensity peaks in Radon space. By identifying these peak coordinates:
- **True Vessel Course**: Established with $0.5^\circ$ angular precision.
- **180° Heading Ambiguity Resolution**: The turbulent wake always extends backward from the stern, resolving the directional vector.
- **Speed-Through-Water Estimation**: Calculated from the wavelength $\lambda_{\text{wake}}$ of the transverse Kelvin wave crests:
  $$V_{\text{water}} = \sqrt{\frac{g \cdot \lambda_{\text{wake}}}{2\pi}}$$

---

## 🛡️ 5. AIS Speed & Course Spoofing Detection

Malicious operators, sanction evaders, and pirate fishing vessels frequently manipulate AIS transmissions—either by broadcasting false GPS coordinates, claiming stationary anchorage ($0\text{ kn}$) while underway, or deliberately offset course data.

### Cross-Sensor Discrepancy Checks
The engine correlates broadcast AIS kinematics against the radar-derived wake parameters:
1. **Speed Spoofing (`AIS_SPEED_SPOOFED`)**:
   $$|V_{\text{AIS}} - V_{\text{wake}}| > 3.0\text{ knots}$$
   *Example: Vessel broadcasts $0.2\text{ kn}$ (claiming anchored) while radar wake measures $14.1\text{ kn}$.*
2. **Course Spoofing (`AIS_HEADING_SPOOFED`)**:
   $$|\theta_{\text{AIS}} - \theta_{\text{wake}}| > 45.0^\circ$$
   *Example: Vessel reports heading East ($090^\circ$) while physical wake confirms course North-West ($315^\circ$).*

When triggered, an advisory tag is appended to the contact dossier, alerting analysts to deceptive intent.

---

## 🛰️ 6. Sentinel-2 Optical Cross-Validation

Whenever cloud-free **Sentinel-2 MSI (Multispectral Instrument)** passes coincide with the Sentinel-1 acquisition window (within $\pm 12\text{ hours}$):
1. **NDWI (Normalized Difference Water Index)**:
   $$\text{NDWI} = \frac{\text{Green} - \text{NIR}}{\text{Green} + \text{NIR}}$$
   Isolates pure water bodies and separates vessel superstructures from surrounding sea surface.
2. **True-Color RGB Chips**: Crops a matching $10\text{ m}$ RGB chip centered on the SAR target.
3. **Visual Confirmation**: Provides analysts with visual validation of vessel superstructure, deck cargo, and paint color.

---

## 🔄 7. Repeat-Pass SAR Coherence & Change Detection

By aligning two consecutive Sentinel-1 radar passes over the same Area of Interest ($T_1$ and $T_2$, spaced by the 6 or 12-day orbital repeat cycle):
- **Log-Ratio Amplitude Differencing**:
  $$D_{\text{ratio}}(x, y) = 10 \cdot \log_{10} \left( \frac{\sigma^\circ_{T_2}(x, y)}{\sigma^\circ_{T_1}(x, y)} \right)$$
- **Target State Categorization**:
  - **New Arrival**: Target present at $T_2$ but absent at $T_1$.
  - **Departure**: Target present at $T_1$ but absent at $T_2$.
  - **Persistent / Anchored**: Target detected in both passes at stationary coordinates.

---

## 🎯 Next Steps

- Explore how intelligence products are rendered in the [**Reporting & Tactical Exports Guide**](reporting-and-exports.html).
- Review command-line and REST API options in the [**CLI & REST API Reference**](cli-and-api.html).
