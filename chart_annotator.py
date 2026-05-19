#!/usr/bin/env python3
"""
Chart Annotator - Marks technical analysis levels on trading charts
"""

from PIL import Image, ImageDraw, ImageFont
import os

def annotate_chart(input_image_path, output_image_path):
    """
    Annotate the NIFTY chart with technical analysis levels
    """
    # Open the original image
    img = Image.open(input_image_path)
    draw = ImageDraw.Draw(img)
    
    # Get image dimensions
    width, height = img.size
    
    # Try to load a font, fallback to default if not available
    try:
        font_large = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 20)
        font_medium = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 16)
        font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)
    except:
        font_large = ImageFont.load_default()
        font_medium = ImageFont.load_default()
        font_small = ImageFont.load_default()
    
    # Define colors
    resistance_color = (255, 0, 0, 200)  # Red for resistance
    support_color = (0, 255, 0, 200)     # Green for support
    current_price_color = (255, 255, 0, 255)  # Yellow for current price
    text_bg_color = (0, 0, 0, 180)       # Semi-transparent black for text background
    arrow_color = (255, 255, 255, 255)   # White for arrows
    
    # Based on the chart analysis, map price levels to pixel coordinates
    # The chart shows price range from approximately 23,640 to 23,800
    # Y-axis spans roughly from pixel 40 to 320 for the price area
    
    # Price to pixel mapping (approximate based on chart)
    price_min = 23640
    price_max = 23800
    y_min = 320  # Bottom of price chart area
    y_max = 40   # Top of price chart area
    
    def price_to_y(price):
        """Convert price level to Y coordinate"""
        ratio = (price - price_min) / (price_max - price_min)
        return y_max + (y_min - y_max) * (1 - ratio)
    
    # Define key levels
    levels = {
        'resistance': 23786,
        'current': 23715,
        'support1': 23700,
        'support2': 23680,
        'support3': 23645
    }
    
    # Draw Resistance Level (23,786)
    y_resistance = price_to_y(levels['resistance'])
    draw.line([(80, y_resistance), (width-50, y_resistance)], fill=resistance_color, width=3)
    draw.rectangle([(width-250, y_resistance-25), (width-50, y_resistance-5)], fill=text_bg_color)
    draw.text((width-245, y_resistance-23), "R: 23,786 (Strong Resistance)", fill=resistance_color, font=font_medium)
    
    # Draw Current Price Level (23,715)
    y_current = price_to_y(levels['current'])
    draw.line([(80, y_current), (width-50, y_current)], fill=current_price_color, width=2)
    draw.rectangle([(width-250, y_current+5), (width-50, y_current+25)], fill=text_bg_color)
    draw.text((width-245, y_current+7), "Current: 23,715.15", fill=current_price_color, font=font_medium)
    
    # Draw Support Level 1 (23,700)
    y_support1 = price_to_y(levels['support1'])
    draw.line([(80, y_support1), (width-50, y_support1)], fill=support_color, width=2)
    draw.rectangle([(width-250, y_support1+5), (width-50, y_support1+25)], fill=text_bg_color)
    draw.text((width-245, y_support1+7), "S1: 23,700", fill=support_color, font=font_small)
    
    # Draw Support Level 2 (23,680)
    y_support2 = price_to_y(levels['support2'])
    draw.line([(80, y_support2), (width-50, y_support2)], fill=support_color, width=2)
    draw.rectangle([(width-250, y_support2+5), (width-50, y_support2+25)], fill=text_bg_color)
    draw.text((width-245, y_support2+7), "S2: 23,680 (Key Support)", fill=support_color, font=font_small)
    
    # Draw Support Level 3 (23,645)
    y_support3 = price_to_y(levels['support3'])
    draw.line([(80, y_support3), (width-50, y_support3)], fill=support_color, width=2)
    draw.rectangle([(width-250, y_support3+5), (width-50, y_support3+25)], fill=text_bg_color)
    draw.text((width-245, y_support3+7), "S3: 23,645", fill=support_color, font=font_small)
    
    # Add title annotation
    draw.rectangle([(10, 10), (450, 60)], fill=text_bg_color)
    draw.text((15, 15), "NIFTY Technical Analysis", fill=(255, 255, 255), font=font_large)
    draw.text((15, 40), "Range: 23,680 - 23,786 | Bias: Neutral", fill=(200, 200, 200), font=font_small)
    
    # Add indicator annotations
    # RSI annotation
    draw.rectangle([(80, 350), (350, 395)], fill=text_bg_color)
    draw.text((85, 355), "RSI: 52.79 (Neutral)", fill=(255, 200, 0), font=font_medium)
    draw.text((85, 375), "No extreme conditions", fill=(200, 200, 200), font=font_small)
    
    # MACD annotation
    draw.rectangle([(80, 540), (400, 585)], fill=text_bg_color)
    draw.text((85, 545), "MACD: -1.21 (Mild Bearish)", fill=(255, 100, 100), font=font_medium)
    draw.text((85, 565), "Converging - Weakening momentum", fill=(200, 200, 200), font=font_small)
    
    # Add trading zone annotations
    # Bullish zone arrow
    arrow_x = 100
    draw.polygon([(arrow_x, y_resistance-40), (arrow_x-10, y_resistance-50), (arrow_x+10, y_resistance-50)], 
                 fill=(0, 255, 0))
    draw.rectangle([(arrow_x-80, y_resistance-75), (arrow_x+80, y_resistance-50)], fill=text_bg_color)
    draw.text((arrow_x-75, y_resistance-72), "Breakout Zone", fill=(0, 255, 0), font=font_small)
    draw.text((arrow_x-75, y_resistance-58), "Target: 23,820+", fill=(0, 255, 0), font=font_small)
    
    # Bearish zone arrow
    arrow_x = 100
    draw.polygon([(arrow_x, y_support2+40), (arrow_x-10, y_support2+50), (arrow_x+10, y_support2+50)], 
                 fill=(255, 0, 0))
    draw.rectangle([(arrow_x-80, y_support2+50), (arrow_x+80, y_support2+75)], fill=text_bg_color)
    draw.text((arrow_x-75, y_support2+52), "Breakdown Zone", fill=(255, 0, 0), font=font_small)
    draw.text((arrow_x-75, y_support2+64), "Target: 23,650-", fill=(255, 0, 0), font=font_small)
    
    # Add consolidation zone shading
    y_top = price_to_y(levels['resistance'])
    y_bottom = price_to_y(levels['support2'])
    
    # Create a semi-transparent overlay for the range
    overlay = Image.new('RGBA', img.size, (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    overlay_draw.rectangle([(80, y_top), (width-50, y_bottom)], 
                          fill=(128, 0, 128, 30), outline=(128, 0, 128, 100), width=2)
    
    # Composite the overlay
    img = Image.alpha_composite(img.convert('RGBA'), overlay)
    draw = ImageDraw.Draw(img)
    
    # Add range annotation
    mid_y = (y_top + y_bottom) / 2
    draw.rectangle([(width-400, mid_y-15), (width-50, mid_y+15)], fill=text_bg_color)
    draw.text((width-395, mid_y-12), "CONSOLIDATION RANGE (106 pts)", 
             fill=(200, 150, 255), font=font_medium)
    
    # Save the annotated image
    img = img.convert('RGB')
    img.save(output_image_path, quality=95)
    print(f"✅ Annotated chart saved to: {output_image_path}")
    
    return output_image_path


def main():
    """Main function to run the chart annotator"""
    # Get the directory of this script
    script_dir = os.path.dirname(os.path.abspath(__file__))
    
    # Input image path
    input_image = os.path.join(script_dir, "logo (1).png")
    
    # Output image path
    output_image = os.path.join(script_dir, "annotated_chart.png")
    
    # Check if input image exists
    if not os.path.exists(input_image):
        print(f"❌ Error: Input image not found at {input_image}")
        print("Please ensure your chart image is saved as 'logo (1).png' in the same directory")
        return
    
    print("🎨 Starting chart annotation...")
    print(f"📂 Input: {input_image}")
    print(f"📂 Output: {output_image}")
    
    # Annotate the chart
    annotate_chart(input_image, output_image)
    
    print("\n✨ Chart annotation complete!")
    print(f"\n📊 Annotated chart includes:")
    print("   • Resistance level at 23,786")
    print("   • Current price at 23,715")
    print("   • Support levels at 23,700, 23,680, 23,645")
    print("   • RSI and MACD indicator summaries")
    print("   • Breakout/Breakdown zones")
    print("   • Consolidation range highlighting")


if __name__ == "__main__":
    main()
