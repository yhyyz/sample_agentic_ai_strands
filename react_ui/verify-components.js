const fs = require('fs');
const path = require('path');

console.log('🔍 Verifying component files...\n');

const files = [
  'components/chat/ChatInterface.tsx',
  'components/chat/ToolCallDisplay.tsx', 
  'components/chat/ChatMessage.tsx',
  'app/chat/page.tsx',
  'app/layout.tsx'
];

files.forEach(file => {
  const filePath = path.join(__dirname, file);
  if (fs.existsSync(filePath)) {
    const content = fs.readFileSync(filePath, 'utf8');
    const lines = content.split('\n').length;
    console.log(`✅ ${file} - ${lines} lines`);
    
    // Check for key indicators
    if (file.includes('ChatInterface')) {
      if (content.includes('sidebar') || content.includes('Sidebar')) {
        console.log('   ⚠️  Still contains sidebar references');
      } else {
        console.log('   ✅ Sidebar references removed');
      }
    }
    
    if (file.includes('ToolCallDisplay')) {
      if (content.includes('ToolCallDisplay')) {
        console.log('   ✅ ToolCallDisplay component exists');
      }
    }
    
    if (file.includes('ChatMessage')) {
      if (content.includes('toolCalls')) {
        console.log('   ✅ Supports toolCalls');
      }
    }
  } else {
    console.log(`❌ ${file} - NOT FOUND`);
  }
});

console.log('\n🚀 To test the changes:');
console.log('1. Visit http://localhost:3000/simple-demo for a working demo');
console.log('2. Visit http://localhost:3000/test for component testing');
console.log('3. Visit http://localhost:3000/chat for the main interface');
console.log('\n💡 If you still see the old interface, try:');
console.log('- Hard refresh (Ctrl+F5 or Cmd+Shift+R)');
console.log('- Clear browser cache');
console.log('- Open in incognito/private mode');
