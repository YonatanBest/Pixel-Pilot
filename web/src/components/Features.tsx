import { useRef } from 'react';
import { motion, useScroll, useTransform } from 'framer-motion';
import './Features.css';

const features = [
  {
    title: 'Multimodal Vision',
    desc: 'Gemini Robotics-ER + Local OCR.',
    id: '01'
  },
  {
    title: 'Safety Tiers',
    desc: 'Guidance, Safe, & Auto modes.',
    id: '02'
  },
  {
    title: 'Smart Context',
    desc: 'Active clarification & Turbo execution.',
    id: '03'
  },
  {
    title: 'Agent Desktop',
    desc: 'Isolated workspace sandbox.',
    id: '04'
  },
  {
    title: 'Secure Desktop',
    desc: 'UAC & Admin prompt handling.',
    id: '05'
  }
];

export const Features = () => {
    const targetRef = useRef<HTMLDivElement>(null);
    const { scrollYProgress } = useScroll({
        target: targetRef,
    });

    const x = useTransform(scrollYProgress, [0, 1], ["0%", "-85%"]);

    return (
        <section ref={targetRef} id="features" className="features-section">
            <div className="sticky-wrapper">
                <motion.div style={{ x }} className="features-track">
                    <div className="feature-intro">
                        <h2>CAPABILITIES</h2>
                        <p>A suite of tools designed for total OS control.</p>
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
