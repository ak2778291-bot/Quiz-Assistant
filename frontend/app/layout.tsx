import type { Metadata } from "next";
import "./globals.css";
import Nav from "@/components/Nav";

export const metadata: Metadata = {
  title: "edugen.live — curriculum-grounded adaptive quizzes",
  description:
    "Quiz questions generated only from retrieved NCERT curriculum content, with per-topic proficiency driving the next quiz's difficulty.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>
        <div className="shell">
          <Nav />
          {children}
        </div>
      </body>
    </html>
  );
}
