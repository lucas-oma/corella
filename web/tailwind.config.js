/** @type {import('tailwindcss').Config} */
export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Harvey-inspired: near-white/charcoal surfaces, navy as the sole
        // strong accent. No secondary "brand" colors — status colors below
        // are used sparingly for state, not decoration.
        surface: {
          DEFAULT: "#FAFAF9",
          raised: "#FFFFFF",
          dark: "#12141A",
          "dark-raised": "#181B23",
        },
        ink: {
          DEFAULT: "#12141A",
          muted: "#5B5F6B",
          subtle: "#8B8F99",
          inverted: "#FAFAF9",
        },
        border: {
          DEFAULT: "#E4E4E1",
          dark: "#262A34",
        },
        accent: {
          DEFAULT: "#0B1B33",
          hover: "#152847",
          foreground: "#FAFAF9",
        },
        status: {
          success: "#1F7A4D",
          warning: "#9A6300",
          danger: "#B3261E",
        },
      },
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "sans-serif"],
        serif: ["Newsreader", "ui-serif", "Georgia", "serif"],
      },
      borderRadius: {
        DEFAULT: "8px",
        sm: "6px",
        lg: "12px",
      },
      boxShadow: {
        card: "0 1px 2px 0 rgb(0 0 0 / 0.04)",
      },
    },
  },
  plugins: [],
};
