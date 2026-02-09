import { motion, useScroll, useTransform } from 'framer-motion';
import { Magnetic } from './Magnetic';
import './Hero.css';

export const Hero = () => {
    const { scrollY } = useScroll();
    const y1 = useTransform(scrollY, [0, 500], [0, 200]);
    const y2 = useTransform(scrollY, [0, 500], [0, -100]);

    return (
        <section className="hero-container">
            <div className="hero-content">
                <motion.div style={{ y: y1 }} className="hero-header">
                    <motion.h1 
                        initial={{ opacity: 0, y: 100 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ duration: 1, ease: [0.16, 1, 0.3, 1] }}
                        className="hero-title"
                    >
                        PIXEL
                    </motion.h1>
                    <motion.h1 
                        initial={{ opacity: 0, y: 100 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ duration: 1, delay: 0.1, ease: [0.16, 1, 0.3, 1] }}
                        className="hero-title outline"
                    >
                        PILOT
                    </motion.h1>
                </motion.div>

                <motion.div 
                    style={{ y: y2 }}
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    transition={{ delay: 0.5, duration: 1 }}
                    className="hero-description-wrapper"
                >
                    <p className="hero-desc">
                        A windows automation agent powered by <span className="text-brand-gradient">Gemini</span>.
                        <br />
                        It sees what you see. It does what you say.
                    </p>
                    
                    <div className="hero-actions">
                        <Magnetic strength={0.3}>
                            <button className="btn-mag-primary">
                                Initialize Agent
                            </button>
                        </Magnetic>
                    </div>
                </motion.div>
            </div>
            
            <motion.div 
                className="scroll-indicator"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                transition={{ delay: 1, duration: 1 }}
            >
                <span>SCROLL TO EXPLORE</span>
                <motion.div 
                    className="scroll-line" 
                    animate={{ height: [0, 60, 0], y: [0, 0, 60] }}
                    transition={{ repeat: Infinity, duration: 2, ease: "easeInOut" }}
                />
            </motion.div>
        </section>
    );
};
