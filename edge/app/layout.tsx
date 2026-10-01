import type { ReactNode } from "react";
import { Providers } from "./providers";

export const metadata = {
  title: "Schedule demo",
  description: "Invite-gated BC schedule agent walkthrough",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body
        style={{
          margin: 0,
          fontFamily:
            'ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif',
          background: "#0f1410",
          color: "#e8efe6",
        }}
      >
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
