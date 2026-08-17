---
name: Kinetic Logic
colors:
  surface: '#f8f9ff'
  surface-dim: '#d0daee'
  surface-bright: '#f8f9ff'
  surface-container-lowest: '#ffffff'
  surface-container-low: '#eff3ff'
  surface-container: '#e6eeff'
  surface-container-high: '#dfe9fc'
  surface-container-highest: '#d9e3f7'
  on-surface: '#121c2a'
  on-surface-variant: '#464555'
  inverse-surface: '#273140'
  inverse-on-surface: '#ebf1ff'
  outline: '#777587'
  outline-variant: '#c7c4d8'
  surface-tint: '#4d44e3'
  primary: '#3525cd'
  on-primary: '#ffffff'
  primary-container: '#4f46e5'
  on-primary-container: '#dad7ff'
  inverse-primary: '#c3c0ff'
  secondary: '#58579b'
  on-secondary: '#ffffff'
  secondary-container: '#b6b4ff'
  on-secondary-container: '#454386'
  tertiary: '#7e3000'
  on-tertiary: '#ffffff'
  tertiary-container: '#a44100'
  on-tertiary-container: '#ffd2be'
  error: '#ba1a1a'
  on-error: '#ffffff'
  error-container: '#ffdad6'
  on-error-container: '#93000a'
  primary-fixed: '#e2dfff'
  primary-fixed-dim: '#c3c0ff'
  on-primary-fixed: '#0f0069'
  on-primary-fixed-variant: '#3323cc'
  secondary-fixed: '#e2dfff'
  secondary-fixed-dim: '#c3c0ff'
  on-secondary-fixed: '#140f54'
  on-secondary-fixed-variant: '#413f82'
  tertiary-fixed: '#ffdbcc'
  tertiary-fixed-dim: '#ffb695'
  on-tertiary-fixed: '#351000'
  on-tertiary-fixed-variant: '#7b2f00'
  background: '#f8f9ff'
  on-background: '#121c2a'
  surface-variant: '#d9e3f7'
typography:
  display:
    fontFamily: Inter
    fontSize: 36px
    fontWeight: '700'
    lineHeight: 44px
    letterSpacing: -0.02em
  headline-lg:
    fontFamily: Inter
    fontSize: 24px
    fontWeight: '600'
    lineHeight: 32px
    letterSpacing: -0.01em
  headline-md:
    fontFamily: Inter
    fontSize: 20px
    fontWeight: '600'
    lineHeight: 28px
  body-lg:
    fontFamily: Inter
    fontSize: 16px
    fontWeight: '400'
    lineHeight: 24px
  body-md:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '400'
    lineHeight: 20px
  body-sm:
    fontFamily: Inter
    fontSize: 12px
    fontWeight: '400'
    lineHeight: 18px
  label-md:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '500'
    lineHeight: 20px
  label-sm:
    fontFamily: Inter
    fontSize: 12px
    fontWeight: '600'
    lineHeight: 16px
    letterSpacing: 0.05em
rounded:
  sm: 0.25rem
  DEFAULT: 0.5rem
  md: 0.75rem
  lg: 1rem
  xl: 1.5rem
  full: 9999px
spacing:
  base: 4px
  xs: 8px
  sm: 12px
  md: 16px
  lg: 24px
  xl: 32px
  xxl: 48px
  gutter: 24px
  margin: 32px
---

## Brand & Style

The design system is rooted in **Minimalism** and **Modern Corporate** aesthetics, specifically tailored for high-density SaaS environments where clarity is paramount. The personality is disciplined, professional, and functional, prioritizing information architecture over decorative elements.

The visual narrative focuses on "dense-but-airy" layouts—maximizing data density while maintaining high legibility through generous whitespace and a strict typographic hierarchy. This design system avoids all skeuomorphic tendencies, opting for flat surfaces and crisp 1px lines to define boundaries. The emotional response is one of calm efficiency, designed to reduce cognitive load for users performing complex, repetitive tasks.

## Colors

The palette is restricted to a high-contrast foundation to ensure maximum readability and a clean, clinical feel.

- **Primary (#4F46E5):** A vibrant Indigo used exclusively for primary actions and active states. It serves as the single point of visual focus.
- **Text/Neutral (#121C2A):** A deep Charcoal used for all primary text. It provides better legibility than pure black while maintaining professional gravity.
- **Background (#FFFFFF):** The canvas for the application, used to create a sense of openness.
- **Surface (#F7F8FA):** Used for sidebars, secondary containers, and subtle sectioning to create visual grouping without heavy borders.
- **Border (#E5E7EB):** A light, consistent stroke used for structural definition.

## Typography

This design system utilizes **Inter** exclusively to leverage its systematic, utilitarian nature. 

- **Headlines:** Use tight letter-spacing and heavier weights (600-700) to create a strong visual anchor.
- **Body:** Standardized at 14px for the majority of UI interactions to balance density and readability.
- **Labels:** Use Medium (500) or Semi-Bold (600) weights to differentiate interactive text from static content.
- **Mobile Scaling:** For screens below 768px, `display` and `headline-lg` should scale down by 20% to maintain visual balance within the viewport.

## Layout & Spacing

The system employs a **Fluid Grid** model based on a 4px baseline. 

- **Desktop (1280px+):** 12-column grid with 24px gutters and 32px outer margins. Sidebars are fixed at 280px.
- **Tablet (768px - 1279px):** 8-column grid with 16px gutters. Sidebars collapse to icons or a drawer.
- **Mobile (Up to 767px):** 4-column grid with 16px margins. Content stacks vertically.

Spacing rhythm is strictly even. Vertical rhythm between sections should use `xl` (32px), while internal component spacing uses `sm` (12px) or `md` (16px) to maintain a "dense-but-airy" feel.

## Elevation & Depth

This design system uses **Tonal Layering** and **Low-Contrast Outlines** as primary depth cues rather than shadows.

- **Level 0 (Background):** Pure white (#FFFFFF).
- **Level 1 (Sub-navigation/Sidebar):** Light Gray surface (#F7F8FA).
- **Level 2 (Cards/Content Blocks):** White surface with a 1px #E5E7EB border.
- **Floating (Modals/Popovers):** These are the only elements permitted to use shadows. Shadows should be ultra-subtle: `0px 4px 12px rgba(18, 28, 42, 0.05)`. 

There is no use of gradients or inner shadows. Depth is achieved through the contrast between the surface color and the background.

## Shapes

The shape language is modern and approachable without being overly soft.

- **Standard Containers:** Cards, input fields, and buttons use a **8px (0.5rem)** radius.
- **Large Containers:** Modals and large sections use a **12px (0.75rem)** radius.
- **Tags/Status Badges:** These are strictly **Pill-shaped** (full round) to distinguish them from interactive buttons and structural containers.

## Components

- **Buttons:** Primary buttons are solid Indigo (#4F46E5) with white text. They are flat, with no gradients or shadows. Secondary buttons use the Border color (#E5E7EB) for a stroke with Charcoal text. 
- **Analyze new JD Button:** This specific button includes a 16px '+' icon prefix; all other buttons remain text-only.
- **Input Fields:** 1px #E5E7EB border, 8px radius, with 12px horizontal padding. Focus state is a 1px #4F46E5 border.
- **Chips/Tags:** Pill-shaped, using #F7F8FA background and #121C2A text. For status-specific tags, use low-opacity tints of the status color (e.g., subtle green for "Active").
- **Lists:** Clean rows separated by 1px #E5E7EB bottom borders. Use `body-md` for list items with 16px vertical padding.
- **Cards:** White background, 1px #E5E7EB border, 8px or 12px radius. No shadow.
- **Checkboxes/Radios:** Use Indigo (#4F46E5) for the selected state. Shapes are standard (square for checkbox, circle for radio).
- **Data Tables:** Text-forward design. Headers use `label-sm` (uppercase). Rows use `body-md`. No alternating row colors; use subtle hover states on the entire row.