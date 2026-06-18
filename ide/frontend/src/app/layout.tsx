import type { Metadata } from "next";
import { VT323, Share_Tech_Mono } from "next/font/google";
import "./globals.css";

const vt323 = VT323({
  variable: "--font-vt323",
  weight: "400",
  subsets: ["latin"],
});

const techMono = Share_Tech_Mono({
  variable: "--font-tech-mono",
  weight: "400",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Mycelium | Python-to-Soroban Web IDE",
  description: "A Python-first framework for smart contract development and agentic orchestration on the Stellar network",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className={`${vt323.variable} ${techMono.variable}`}>
      <body>{children}</body>
    </html>
  );
}
