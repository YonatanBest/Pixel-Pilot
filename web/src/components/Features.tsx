import { useRef } from 'react';
import { motion, useScroll, useTransform } from 'framer-motion';
import './Features.css';

const features = [
  {
    title: 'Multi-Provider Live',
    desc: 'High-frequency Live sessions powered by native Realtime models and LiteLLM fallbacks for omni-provider coverage.',
    id: '01'
  },
  {
    title: 'Automated Perception',
    desc: 'Understands on-screen context via high-performance Vision Foundation models and local OCR foundations.',
    id: '02'
  },
  {
    title: 'Isolated Workspaces',
    desc: 'Execution takes place in an isolated desktop sandbox (Agent Desktop) for surgical, reliable automation.',
    id: '03'
  },
  {
    title: 'Secure Desktop Agency',
    desc: 'A robust UAC orchestrator extends control into elevated prompts and protected OS surfaces.',
    id: '04'
  },
  {
    title: 'Adaptive Safety Protocols',
    desc: 'Autonomous execution with granular logic layers: Guidance, Safe, and Full Autonomy modes.',
    id: '05'
  },
  {
    title: 'Native Wake Word',
    desc: 'Near-instant command activation via privacy-first, local "Hey Pixie" wake word detection.',
    id: '06'
  },
  {
    title: 'Biometric Voice ID',
    desc: 'Verify authorized operator presence through secure, local voiceprint enrollment and verification.',
    id: '07'
  },
  {
    title: 'Live Decision Loop',
    desc: 'The agent plans, verifies, and self-corrects in real-time as tasks evolve dynamically on screen.',
    id: '08'
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
