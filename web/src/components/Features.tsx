import { useRef } from 'react';
import { motion, useScroll, useTransform } from 'framer-motion';
import './Features.css';

const features = [
  {
    title: 'Vision-First Automation',
    desc: 'Understands on-screen context with Gemini Robotics-ER and local OCR fallback.',
    id: '01'
  },
  {
    title: 'Gemini Live Sessions',
    desc: 'Default-on live voice and text runtime with reconnect continuity and stop safety.',
    id: '02'
  },
  {
    title: 'UI Automation Core',
    desc: 'Blind-first snapshots, window focus, UI element targeting, and text extraction.',
    id: '03'
  },
  {
    title: 'Adaptive Safety Modes',
    desc: 'Guidance, Safe, and Auto execution designed for different trust levels.',
    id: '04'
  },
  {
    title: 'Live Decision Loop',
    desc: 'Plans, verifies, and corrects actions while tasks are still in motion.',
    id: '05'
  },
  {
    title: 'Agent Desktop',
    desc: 'Runs in an isolated desktop workspace for clean, reliable automation.',
    id: '06'
  },
  {
    title: 'Secure Desktop Coverage',
    desc: 'UAC orchestration extends control into elevated and protected prompts.',
    id: '07'
  }
];

export const Features = () => {
  const targetRef = useRef<HTMLDivElement>(null);
  const { scrollYProgress } = useScroll({
    target: targetRef,
  });

  const x = useTransform(scrollYProgress, [0, 1], ["0%", "-93%"]);

  return (
    <section ref={targetRef} id="features" className="features-section">
      <div className="sticky-wrapper">
        <motion.div style={{ x }} className="features-track">
          <div className="feature-intro">
            <h2>WHY PIXELPILOT</h2>
            <p>Core pillars behind the project and its automation model.</p>
            <span className="scroll-hint">SCROLL &rarr;</span>
          </div>
          {features.map((feature) => (
            <div key={feature.id} className="feature-panel">
              <span className="feature-id">{feature.id}</span>
              <h3 className="feature-title">{feature.title}</h3>
              <p className="feature-desc">{feature.desc}</p>
            </div>
          ))}
        </motion.div>
      </div>
    </section>
  );
};
