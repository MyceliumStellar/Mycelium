import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Mycelium | Python Agent Framework on Stellar",
  description: "The Python-first framework for creating autonomous agents that discover, coordinate, and transact on Stellar.",
  icons: {
    icon: "/mycelium-logo.png",
    shortcut: "/mycelium-logo.png",
    apple: "/mycelium-logo.png",
  },
  openGraph: {
    title: "Mycelium | Python Agent Framework on Stellar",
    description: "The Python-first framework for creating autonomous agents that discover, coordinate, and transact on Stellar.",
    images: ["/mycelium-logo.png"],
  },
  twitter: {
    card: "summary_large_image",
    title: "Mycelium | Python Agent Framework on Stellar",
    description: "The Python-first framework for creating autonomous agents that discover, coordinate, and transact on Stellar.",
    images: ["/mycelium-logo.png"],
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;700&family=Playfair+Display:ital,wght@0,400;0,700;1,400&family=Share+Tech+Mono&family=Space+Grotesk:wght@300;400;500;700&family=VT323&display=swap" rel="stylesheet" />
      </head>
      <body>{children}</body>
    </html>
  );
}
